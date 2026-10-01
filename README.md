# SC4016 Group Project: ransomwatch

Fork of [joshhighet/ransomwatch](https://github.com/joshhighet/ransomwatch) (original README: [.github/UPSTREAM_README.md](.github/UPSTREAM_README.md)).

## Usage

```bash
# 1. Start Tor. Either the project's sidecar container:
sudo docker run -d --name tor -p 9050:9050 ghcr.io/joshhighet/torsocc:latest
#    or your local daemon:  sudo systemctl start tor
# check it works (should print an IsTor:true JSON line)
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip

# 2. Rebuild the image. Needed after this session's code changes (victims.py, pycountry).
sudo docker build -t ransomwatch .

# 3. Make sure the output folder exists
mkdir -p data

# 4. Helper so you don't retype the mounts every time
rw() { sudo docker run --rm --network host \
  -v "$PWD/groups.json:/groups.json" \
  -v "$PWD/posts.json:/posts.json" \
  -v "$PWD/source:/source" \
  -v "$PWD/data:/data" \
  -v "$PWD/docs:/docs" \
  ransomwatch "$@"; }

rw scrape     # fetch every live leak site over Tor → source/*.html, updates groups.json
rw parse      # legacy parsers → posts.json, then builds the structured table → data/
rw markdown   # optional: regenerates the docs/ site pages and graphs

rw victims    # rebuild data/*.csv from the saved pages without re-scraping
rw backfill --since 2026-01-01   # walk incransom/qilin/play/safepay/akira history back to a date + fetch post pages (cached in source/detail/)
rw backfill --name akira,play   # backfill only the named groups (comma separated)
rw backfill --since 2023-01-01 --pages 400   # deep history: posts before 2026 go to data/history.json/.csv, the 2026 table is unchanged
rw add --name <group> --location http://<address>.onion   # track a new site
```

## Analysis

Run on the host, not in docker. Both only read the files above.

```bash
pip install pandas matplotlib jupyter

# Q4: company size & revenue from wikidata, matched by victim website, then by exact company name
python3 companysize.py 2026-01-01   # victims dated on/after this date → data/company_size.csv (clearnet, ~10-20 min)

# Q3: rebrand signals (handovers, shared victims, shared contact IDs, same site kit, seizure notices)
jupyter notebook analysis/rebrands_check.ipynb   # also writes analysis/output/rebrands_*.csv

# re-posted / repurposed leaks (needs the deep backfill above for the 2023-25 side)
jupyter notebook analysis/repurposed_leaks.ipynb   # also writes analysis/output/repurposed_*.csv
```
