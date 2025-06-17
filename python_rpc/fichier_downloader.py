import os
import re
import time
import random
import tempfile
import requests
from urllib.parse import urlparse
from http_downloader import HttpDownloader

try:
    from torpy.http.requests import TorRequests
    TORPY_AVAILABLE = True
except ImportError:
    TORPY_AVAILABLE = False
    print("Warning: torpy library not found. 1fichier downloads will not work properly.")
    print("Please install torpy with: pip install torpy")

class FichierDownloader:
    def __init__(self):
        self.http_downloader = HttpDownloader()
        self.current_url = None
        self.save_path = None
        self.filename = None
        self.direct_url = None
        self.max_attempts = 10

    def _get_random_user_agent(self):
        """Return a random user agent to avoid detection"""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0"
        ]
        return random.choice(user_agents)

    def _check_tor_available(self):
        """Check if Tor is available"""
        if not TORPY_AVAILABLE:
            raise Exception("torpy library is required for 1fichier downloads")

    def _get_direct_link(self, url):
        """Get the direct download link from 1fichier"""
        self._check_tor_available()

        # Extract filename from URL if possible
        parsed_url = urlparse(url)
        path_parts = parsed_url.path.split('/')
        if len(path_parts) > 0:
            self.filename = path_parts[-1]
            if not self.filename:
                self.filename = "download"
        else:
            self.filename = "download"

        # Try to get direct link with multiple attempts
        for attempt in range(self.max_attempts):
            try:
                print(f"Attempt {attempt+1}/{self.max_attempts} to get 1fichier direct link")

                # Set headers to mimic a browser
                headers = {
                    "User-Agent": self._get_random_user_agent(),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Referer": "https://1fichier.com/"
                }

                # Create a new TorRequests instance for each attempt
                with TorRequests() as tor_requests:
                    # Get a new Tor circuit
                    with tor_requests.get_session() as session:
                        # Get the download page
                        response = session.get(url, headers=headers)

                        # Check if we're being rate limited or need to wait
                        if "You must wait" in response.text or "Warning !" in response.text or "Attention !" in response.text:
                            print("Rate limit detected, trying with a new Tor circuit...")
                            time.sleep(1)
                            continue

                        # Try to extract the filename if not already set
                        if self.filename == "download":
                            filename_match = re.search(r'>Filename :<.*<td class="normal">(.*)</td>', response.text)
                            if filename_match:
                                self.filename = filename_match.group(1)

                        # Check if we can find the download form
                        adz_match = re.search(r'name="adz" value="([^"]+)"', response.text)
                        if adz_match:
                            adz_value = adz_match.group(1)

                            # Submit the download form
                            form_data = {"submit": "Download", "adz": adz_value}
                            download_response = session.post(url, data=form_data, headers=headers, allow_redirects=False)

                            # Check for redirect which contains the direct download link
                            if download_response.status_code in (302, 303) and 'Location' in download_response.headers:
                                direct_url = download_response.headers['Location']
                                print(f"Successfully obtained direct download link: {direct_url}")
                                return direct_url

                            # If no redirect, try to find the download link in the response
                            download_link_match = re.search(r'<a href="(https?://[^"]+)"[^>]*>Click here to download the file</a>', download_response.text)
                            if download_link_match:
                                direct_url = download_link_match.group(1)
                                print(f"Successfully obtained direct download link: {direct_url}")
                                return direct_url

                print("Failed to extract download link, retrying...")
                time.sleep(1)

            except Exception as e:
                print(f"Error during attempt {attempt+1}: {str(e)}")
                time.sleep(1)

        raise Exception("Failed to get direct download link after multiple attempts")

    def start_download(self, url, save_path, header=None, out=None):
        """Start downloading a file from 1fichier"""
        self.current_url = url
        self.save_path = save_path

        try:
            # Get the direct download link
            direct_url = self._get_direct_link(url)
            self.direct_url = direct_url

            # Use the HttpDownloader to download the file
            if out is None and self.filename:
                out = self.filename

            # Create custom header for the download
            custom_header = f"User-Agent: {self._get_random_user_agent()}"
            if header:
                custom_header += f"\n{header}"

            # Start the actual download using HttpDownloader
            self.http_downloader.start_download(direct_url, save_path, custom_header, out)

            return {
                "status": "downloading",
                "message": f"Started downloading {self.filename} from 1fichier"
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to start download: {str(e)}"
            }

    def pause_download(self):
        """Pause the current download"""
        return self.http_downloader.pause_download()

    def cancel_download(self):
        """Cancel the current download"""
        result = self.http_downloader.cancel_download()
        return result

    def get_download_status(self):
        """Get the status of the current download"""
        return self.http_downloader.get_download_status()
