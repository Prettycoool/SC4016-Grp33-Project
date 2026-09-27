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
rw backfill --since 2026-01-01   # walk incransom/qilin/play/safepay/akira/krybit history back to a date + fetch post pages (cached in source/detail/)
rw backfill --name krybit       # backfill only the named groups (comma separated)
rw add --name <group> --location http://<address>.onion   # track a new site
```
