start docker

sudo systemctl start docker
sudo systemctl enable docker
sudo docker run -d -p 9050:9050 ghcr.io/joshhighet/torsocc:latest

sudo apt install firefox-esr
# then grab a geckodriver release matching your arch and put it in $PATH, e.g.:
curl -L $(curl -sL https://api.github.com/repos/mozilla/geckodriver/releases/latest | jq -r '.assets[].browser_download_url' | grep linux64.tar.gz$) | tar -xz
chmod +x geckodriver && sudo mv geckodriver /usr/local/bin

