"""
Script ini mengambil data bahasa pemrograman dari semua repository GitHub
milik user, menghitung persentasenya, lalu menuliskannya ke README.md
di antara marker <!--START_LANGUAGES--> dan <!--END_LANGUAGES-->.

Tampilan yang dihasilkan:
  - Satu SVG mandiri (assets/language-stats.svg) berisi bar proporsional
    ala language-bar bawaan GitHub + legend berwarna, digambar langsung
    oleh script ini (tidak bergantung layanan badge eksternal seperti
    shields.io, jadi lebih cepat dan tidak bisa "putus" kalau layanan
    pihak ketiga down).
  - Baris ringkasan (jumlah bahasa, jumlah repo, waktu update) sebagai
    teks biasa di README, terpisah dari gambar supaya history git tetap
    rapi (gambar hanya berubah kalau datanya benar-benar berubah).

Dijalankan otomatis lewat GitHub Actions (lihat update-languages.yml).
"""

import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

import requests

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------

USERNAME = os.environ.get("GH_USERNAME", "")
TOKEN = os.environ.get("GH_TOKEN", "")
README_PATH = "README.md"
SVG_PATH = "assets/language-stats.svg"

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
# Warna bahasa GitHub Linguist (hex tanpa #)
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
DEFAULT_COLOR = "8b949e"  # abu-abu netral untuk bahasa yang tidak ada di daftar

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
            print("Rate limit tercapai. Menunggu 60s...")
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
# Generate SVG (bar proporsional + legend) — menggantikan badge per-bahasa
# ---------------------------------------------------------------------------


def generate_language_bar_svg(totals, width=760):
    """
    Buat satu SVG mandiri: bar proporsional (mirip language-bar bawaan
    GitHub di halaman repo) diikuti legend berwarna dalam grid rapi.
    Semua digambar langsung sebagai <rect>/<text> — tidak memanggil
    gambar/layanan eksternal apa pun, jadi konsisten dan cepat dimuat.

    Return (svg_markup, jumlah_bahasa) atau (None, 0) kalau tidak ada data.
    """
    total_bytes = sum(totals.values())
    if total_bytes == 0:
        return None, 0

    items = []
    for lang, byte_count in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        percent = (byte_count / total_bytes) * 100
        if round(percent, 1) > 0.0:
            items.append((lang, percent))

    if not items:
        return None, 0

    bar_height = 10
    top_gap = 20
    row_height = 22
    cols = 3 if len(items) > 4 else len(items)
    col_width = width / cols
    rows = math.ceil(len(items) / cols)
    height = bar_height + top_gap + rows * row_height + 2

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif">'
    ]

    # Bar proporsional: ujung membulat, sambungan antar-warna tegas
    svg.append(
        f'<clipPath id="barClip"><rect width="{width}" height="{bar_height}" '
        f'rx="{bar_height / 2}"/></clipPath>'
    )
    svg.append('<g clip-path="url(#barClip)">')
    x = 0.0
    for lang, percent in items:
        seg_width = (percent / 100) * width
        color = LANGUAGE_COLORS.get(lang, DEFAULT_COLOR)
        svg.append(f'<rect x="{x:.2f}" width="{seg_width:.2f}" height="{bar_height}" fill="#{color}"/>')
        x += seg_width
    svg.append("</g>")

    # Legend grid: kotak warna + nama bahasa + persentase, sejajar rapi
    for i, (lang, percent) in enumerate(items):
        col, row = i % cols, i // cols
        cx = col * col_width
        cy = bar_height + top_gap + row * row_height
        color = LANGUAGE_COLORS.get(lang, DEFAULT_COLOR)
        label = xml_escape(lang)
        svg.append(
            f'<rect x="{cx:.1f}" y="{cy - 9:.1f}" width="10" height="10" rx="2" fill="#{color}"/>'
            f'<text x="{cx + 16:.1f}" y="{cy:.1f}" font-size="12" fill="#{DEFAULT_COLOR}" '
            f'dominant-baseline="middle">{label} · {percent:.1f}%</text>'
        )

    svg.append("</svg>")
    return "".join(svg), len(items)


def save_svg(svg_markup, path=SVG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(svg_markup)
    return path


# ---------------------------------------------------------------------------
# Generate output Markdown
# ---------------------------------------------------------------------------


def build_markdown(svg_path, repo_count, lang_count):
    """Blok teks yang ditulis di antara marker START/END_LANGUAGES."""
    now = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")
    lines = [
        f'<img src="./{svg_path}" alt="Most used languages" />',
        "",
        f"_{lang_count} bahasa terdeteksi dari {repo_count} repository publik "
        f"· diperbarui {now}_",
    ]
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

    print("Generating language bar SVG...")
    svg_markup, lang_count = generate_language_bar_svg(totals)

    if svg_markup is None:
        markdown_block = "_Belum ada data bahasa pemrograman._"
    else:
        svg_path = save_svg(svg_markup)
        markdown_block = build_markdown(svg_path, repo_count, lang_count)
        print(f"  SVG disimpan ke {svg_path}")

    print("Writing to README.md...")
    update_readme(markdown_block)

    print("✅ README.md successfully updated.")


if __name__ == "__main__":
    main()