"""
Script ini mengambil data bahasa pemrograman dari semua repository GitHub
milik user, menghitung persentasenya, lalu menuliskannya ke README.md
di antara marker <!--START_LANGUAGES--> dan <!--END_LANGUAGES-->.

Fitur:
  - Shields.io badges dengan warna GitHub Linguist & logo
  - Progress bar visual (Unicode)
  - Tabel Markdown yang rapi
  - Statistik ringkasan & timestamp
  - Rate-limit aware & error handling

Dijalankan otomatis lewat GitHub Actions (lihat update-languages.yml).
"""

import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------

USERNAME = os.environ.get("GH_USERNAME", "")
TOKEN = os.environ.get("GH_TOKEN", "")
README_PATH = "README.md"

START_MARKER = "<!--START_LANGUAGES-->"
END_MARKER = "<!--END_LANGUAGES-->"

if not USERNAME:
    print("ERROR: GH_USERNAME belum di-set.")
    sys.exit(1)

HEADERS = {"Accept": "application/vnd.github+json"}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"
else:
    print("WARNING: GH_TOKEN tidak di-set. Rate limit = 60 req/jam.")

# ---------------------------------------------------------------------------
# Warna bahasa GitHub Linguist  (hex tanpa #)
# https://github.com/ozh/github-colors
# ---------------------------------------------------------------------------

LANGUAGE_COLORS = {
    "Python": "3572A5",
    "JavaScript": "f1e05a",
    "TypeScript": "3178c6",
    "Java": "b07219",
    "C": "555555",
    "C++": "f34b7d",
    "C#": "178600",
    "Go": "00ADD8",
    "Rust": "dea584",
    "Ruby": "701516",
    "PHP": "4F5D95",
    "Swift": "F05138",
    "Kotlin": "A97BFF",
    "Dart": "00B4AB",
    "Scala": "c22d40",
    "R": "198CE7",
    "Lua": "000080",
    "Shell": "89e051",
    "PowerShell": "012456",
    "Perl": "0298c3",
    "Haskell": "5e5086",
    "Elixir": "6e4a7e",
    "Clojure": "db5855",
    "Erlang": "B83998",
    "Julia": "a270ba",
    "Objective-C": "438eff",
    "HTML": "e34c26",
    "CSS": "563d7c",
    "SCSS": "c6538c",
    "Vue": "41b883",
    "Svelte": "ff3e00",
    "Jupyter Notebook": "DA5B0B",
    "Dockerfile": "384d54",
    "Makefile": "427819",
    "CMake": "DA3434",
    "Nix": "7e7eff",
    "HCL": "844FBA",
    "Zig": "ec915c",
    "V": "4f87c4",
    "Nim": "ffc200",
    "OCaml": "3be133",
    "F#": "b845fc",
    "Assembly": "6E4C13",
    "MATLAB": "e16737",
    "TeX": "3D6117",
    "Vim Script": "199f4b",
}

# Logo Simple Icons (untuk shields.io) — hanya bahasa dengan ikon yang tersedia
LANGUAGE_LOGOS = {
    "Python": "python",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
    "Java": "openjdk",
    "C": "c",
    "C++": "cplusplus",
    "C#": "csharp",
    "Go": "go",
    "Rust": "rust",
    "Ruby": "ruby",
    "PHP": "php",
    "Swift": "swift",
    "Kotlin": "kotlin",
    "Dart": "dart",
    "Scala": "scala",
    "R": "r",
    "Lua": "lua",
    "Shell": "gnubash",
    "PowerShell": "powershell",
    "Perl": "perl",
    "Haskell": "haskell",
    "Elixir": "elixir",
    "Julia": "julia",
    "HTML": "html5",
    "CSS": "css3",
    "Vue": "vuedotjs",
    "Svelte": "svelte",
    "Jupyter Notebook": "jupyter",
    "Dockerfile": "docker",
    "Nim": "nim",
    "OCaml": "ocaml",
    "Zig": "zig",
}

# ---------------------------------------------------------------------------
# Fungsi utilitas
# ---------------------------------------------------------------------------


def check_rate_limit():
    """Cek sisa rate limit, jika hampir habis tunggu reset."""
    resp = requests.get("https://api.github.com/rate_limit", headers=HEADERS)
    if resp.status_code == 200:
        data = resp.json()["resources"]["core"]
        remaining = data["remaining"]
        reset_at = data["reset"]
        if remaining < 10:
            wait = max(reset_at - int(time.time()), 0) + 5
            print(f"Rate limit hampir habis ({remaining} sisa). Menunggu {wait}s...")
            time.sleep(wait)
        else:
            print(f"Rate limit OK: {remaining} request tersisa.")


def api_get(url, params=None):
    """GET request ke GitHub API dengan error handling."""
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            print(f"Rate limit tercapai. Menunggu 60s...")
            time.sleep(60)
            return api_get(url, params)  # retry sekali
        print(f"HTTP Error {resp.status_code} untuk {url}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Request gagal untuk {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Ambil data dari GitHub API
# ---------------------------------------------------------------------------


def get_all_repos():
    """Ambil semua repo publik milik user (paginated)."""
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{USERNAME}/repos"
        params = {"per_page": 100, "page": page, "type": "owner"}
        data = api_get(url, params)
        if not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
    return repos


def get_language_bytes(repo_full_name):
    """Ambil breakdown bahasa (bytes) untuk satu repo."""
    url = f"https://api.github.com/repos/{repo_full_name}/languages"
    data = api_get(url)
    return data if data else {}


def aggregate_languages(repos):
    """Jumlahkan total byte per bahasa dari semua repo non-fork."""
    totals = {}
    repo_count = 0
    for repo in repos:
        if repo.get("fork"):
            continue
        repo_count += 1
        langs = get_language_bytes(repo["full_name"])
        for lang, byte_count in langs.items():
            totals[lang] = totals.get(lang, 0) + byte_count
    return totals, repo_count


# ---------------------------------------------------------------------------
# Generate output Markdown
# ---------------------------------------------------------------------------


def make_badge_url(lang, color):
    """Buat URL shields.io badge untuk bahasa."""
    # Encode karakter khusus untuk URL
    safe_lang = lang.replace("-", "--").replace(" ", "_").replace("#", "%23").replace("+", "%2B")
    logo = LANGUAGE_LOGOS.get(lang, "")

    base = f"https://img.shields.io/badge/{safe_lang}-{color}"
    params = "style=flat-square&logoColor=white"
    if logo:
        params += f"&logo={logo}"

    return f"{base}?{params}"


def make_bar(percent, width=20):
    """Buat progress bar Unicode, dengan clamping aman."""
    filled = max(0, min(width, round(percent / (100 / width))))
    return "█" * filled + "░" * (width - filled)


def build_markdown(totals, repo_count):
    """Generate a clean, visually appealing Markdown block for language stats."""
    total_bytes = sum(totals.values())
    if total_bytes == 0:
        return "_No programming language data available._"

    sorted_langs = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    
    lines = []
    
    # Calculate percentages and filter out anything that rounds to 0.0%
    valid_langs = []
    for lang, byte_count in sorted_langs:
        percent = (byte_count / total_bytes) * 100
        if round(percent, 1) > 0.0:
            valid_langs.append((lang, percent))

    total_langs_found = len(valid_langs)

    lines.append('<div>')
    lines.append('<br>')

    for lang, percent in valid_langs:
        color = LANGUAGE_COLORS.get(lang, "333333")
        badge_url = make_badge_url(lang, color)
        bar = make_bar(percent)
        
        # Using non-breaking spaces and clean formatting for a balanced look
        lines.append(
            f"![]({badge_url}) &nbsp; `{bar}` &nbsp; **{percent:.1f}%**  <br/>"
        )

    lines.append('<br>')

    # Summary statistics in Professional English
    now = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")
    lines.append(
        f"> 📊 **Statistics:** Analysed **{repo_count} public repositories** "
        f"• Detected **{total_langs_found} primary languages**<br/>"
        f"> 🕒 **Last Updated:** {now}"
    )

    lines.append('</div>')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tulis ke README.md
# ---------------------------------------------------------------------------


def update_readme(markdown_block):
    """Timpa konten di antara marker START/END_LANGUAGES."""
    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        re.DOTALL,
    )
    replacement = f"{START_MARKER}\n\n{markdown_block}\n\n{END_MARKER}"

    if pattern.search(content):
        new_content = pattern.sub(replacement, content)
    else:
        new_content = content + f"\n\n{replacement}\n"

    with open(README_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print(f"=== Update Language Stats for @{USERNAME} ===")

    check_rate_limit()

    print("Fetching repository list...")
    repos = get_all_repos()
    print(f"  Found {len(repos)} repositories.")

    print("Aggregating language data...")
    totals, repo_count = aggregate_languages(repos)
    print(f"  {len(totals)} languages found across {repo_count} non-forked repos.")

    print("Generating Markdown...")
    markdown_block = build_markdown(totals, repo_count)

    print("Writing to README.md...")
    update_readme(markdown_block)

    print("✅ README.md successfully updated.")


if __name__ == "__main__":
    main()