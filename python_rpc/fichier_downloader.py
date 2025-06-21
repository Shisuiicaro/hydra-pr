import os
import re
import time
import random
import tempfile
import requests
import concurrent.futures
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
        self.max_parallel_attempts = 5  # Número de circuitos Tor paralelos
        self.circuit_timeout = 30  # Timeout para cada tentativa de circuito em segundos

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
            
    def _check_service_availability(self):
        """Check if 1fichier service is available before attempting downloads"""
        try:
            # Use a direct request without Tor to check service availability
            headers = {"User-Agent": self._get_random_user_agent()}
            response = requests.get("https://1fichier.com", headers=headers, timeout=10)
            
            if response.status_code == 200:
                print("1fichier service is available")
                return True
            else:
                print(f"1fichier service returned status code: {response.status_code}")
                return False
        except Exception as e:
            print(f"Error checking 1fichier service availability: {str(e)}")
            return False

    def _extract_filename_from_url(self, url):
        """Extract filename from URL if possible"""
        parsed_url = urlparse(url)
        path_parts = parsed_url.path.split('/')
        if len(path_parts) > 0:
            filename = path_parts[-1]
            if not filename:
                filename = "download"
        else:
            filename = "download"
        return filename

    def _try_single_circuit(self, url, attempt_id):
        """Try to get direct link using a single Tor circuit"""
        try:
            print(f"Circuit attempt {attempt_id} started")
            
            # Set headers to mimic a browser
            headers = {
                "User-Agent": self._get_random_user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://1fichier.com/",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0"
            }

            # Create a new TorRequests instance for this attempt with retry mechanism
            max_tor_retries = 3
            for tor_retry in range(max_tor_retries):
                try:
                    # Create a new TorRequests instance for each retry
                    with TorRequests() as tor_requests:
                        # Get a new Tor circuit with timeout
                        with tor_requests.get_session() as session:
                            # Get the download page
                            start_time = time.time()
                            response = session.get(url, headers=headers, timeout=self.circuit_timeout)
                            
                            # Check if we're being rate limited or need to wait
                            if "You must wait" in response.text or "Warning !" in response.text or "Attention !" in response.text:
                                print(f"Circuit {attempt_id}: Rate limit detected")
                                break  # No need to retry this circuit

                            # Try to extract the filename if not already set
                            local_filename = self.filename
                            if local_filename == "download":
                                filename_match = re.search(r'>Filename :<.*<td class="normal">(.*)</td>', response.text)
                                if filename_match:
                                    local_filename = filename_match.group(1)

                            # Check if we can find the download form
                            adz_match = re.search(r'name="adz" value="([^"]+)"', response.text)
                            if not adz_match:
                                print(f"Circuit {attempt_id}: No download form found")
                                break  # No need to retry this circuit
                                
                            adz_value = adz_match.group(1)

                            # Submit the download form
                            form_data = {"submit": "Download", "adz": adz_value}
                            download_response = session.post(url, data=form_data, headers=headers, allow_redirects=False, timeout=self.circuit_timeout)

                            # Check for redirect which contains the direct download link
                            if download_response.status_code in (302, 303) and 'Location' in download_response.headers:
                                direct_url = download_response.headers['Location']
                                elapsed = time.time() - start_time
                                print(f"Circuit {attempt_id}: Successfully obtained direct link in {elapsed:.2f}s: {direct_url}")
                                return {'url': direct_url, 'filename': local_filename}

                            # If no redirect, try to find the download link in the response
                            download_link_match = re.search(r'<a href="(https?://[^"]+)"[^>]*>Click here to download the file</a>', download_response.text)
                            if download_link_match:
                                direct_url = download_link_match.group(1)
                                elapsed = time.time() - start_time
                                print(f"Circuit {attempt_id}: Successfully obtained direct link in {elapsed:.2f}s: {direct_url}")
                                return {'url': direct_url, 'filename': local_filename}
                            
                            print(f"Circuit {attempt_id}: Failed to extract download link")
                            break  # No need to retry this circuit if we got a response but no link
                            
                except (AssertionError, ConnectionError, TimeoutError) as circuit_error:
                    # These are common Tor circuit errors that can be retried
                    if tor_retry < max_tor_retries - 1:
                        print(f"Circuit {attempt_id}: Tor circuit error (retry {tor_retry+1}/{max_tor_retries}): {str(circuit_error)}")
                        time.sleep(0.5)  # Short delay before retry
                        continue
                    else:
                        print(f"Circuit {attempt_id}: Max Tor retries reached")
                        break
                except Exception as other_error:
                    print(f"Circuit {attempt_id}: Unexpected error in Tor circuit: {str(other_error)}")
                    break
                
                # If we got here without exceptions, no need to retry
                break
                
            return None  # Return None if all retries failed or no link was found

        except Exception as e:
            print(f"Circuit {attempt_id}: Error in circuit attempt: {str(e)}")
            return None

    def _get_direct_link(self, url):
        """Get the direct download link from 1fichier using parallel circuits"""
        self._check_tor_available()
        
        # Check if 1fichier service is available before attempting
        if not self._check_service_availability():
            print("Warning: 1fichier service may be unavailable, but will try anyway")

        # Extract filename from URL if possible
        self.filename = self._extract_filename_from_url(url)
        print(f"Attempting to download file: {self.filename}")

        # Track total attempts and successful/failed circuits
        total_attempts = 0
        successful_circuits = 0
        failed_circuits = 0
        batch_delay = 1  # Initial delay between batches (seconds)
        max_batch_delay = 5  # Maximum delay between batches
        start_time = time.time()

        # Try sequential batches of parallel attempts
        for batch in range((self.max_attempts // self.max_parallel_attempts) + 1):
            # Check if we've exceeded max attempts
            if total_attempts >= self.max_attempts:
                break
                
            batch_start_time = time.time()
            print(f"Starting batch {batch+1} of parallel circuit attempts")
            
            # Calculate how many attempts to make in this batch
            remaining_attempts = self.max_attempts - total_attempts
            batch_size = min(self.max_parallel_attempts, remaining_attempts)
            
            if batch_size <= 0:
                break
                
            # Create a thread pool for parallel attempts
            with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
                # Submit parallel circuit attempts
                future_to_circuit = {}
                for i in range(batch_size):
                    attempt_id = total_attempts + i + 1
                    future = executor.submit(self._try_single_circuit, url, attempt_id)
                    future_to_circuit[future] = attempt_id
                
                # Update total attempts
                total_attempts += batch_size
                batch_results = 0
                
                # Process results as they complete
                for future in concurrent.futures.as_completed(future_to_circuit):
                    circuit_id = future_to_circuit[future]
                    try:
                        result = future.result()
                        if result:
                            # Update filename if found
                            if result['filename'] != "download":
                                self.filename = result['filename']
                            elapsed = time.time() - start_time
                            print(f"Successfully found direct link after {total_attempts} total attempts ({elapsed:.2f}s)")
                            return result['url']
                        else:
                            failed_circuits += 1
                    except Exception as exc:
                        print(f"Circuit {circuit_id} generated an exception: {exc}")
                        failed_circuits += 1
            
            batch_elapsed = time.time() - batch_start_time
            print(f"Batch {batch+1} completed in {batch_elapsed:.2f}s with {batch_size} circuits")
            
            # If we've reached the maximum number of attempts, break
            if total_attempts >= self.max_attempts:
                print(f"Reached maximum number of attempts ({self.max_attempts})")
                break
            
            # Implement exponential backoff between batches
            if batch > 0:  # Don't increase delay after first batch
                batch_delay = min(batch_delay * 1.5, max_batch_delay)  # Exponential backoff with cap
                
            print(f"All circuits in batch {batch+1} failed, waiting {batch_delay:.1f}s before next batch...")
            time.sleep(batch_delay)

        total_elapsed = time.time() - start_time
        print(f"Failed after {total_attempts} attempts in {total_elapsed:.2f}s")
        print(f"Statistics: {failed_circuits} failed circuits, {successful_circuits} successful circuits")
        raise Exception(f"Failed to get direct download link after {total_attempts} attempts ({total_elapsed:.2f}s)")

    def start_download(self, url, save_path, header=None, out=None):
        """Start downloading a file from 1fichier"""
        self.current_url = url
        self.save_path = save_path

        try:
            start_time = time.time()
            print(f"Starting 1fichier download process for {url}")
            
            # Get the direct download link with parallel circuits
            direct_url = self._get_direct_link(url)
            self.direct_url = direct_url
            
            elapsed = time.time() - start_time
            print(f"Successfully obtained direct link in {elapsed:.2f} seconds")

            # Use the HttpDownloader to download the file
            if out is None and self.filename:
                out = self.filename

            # Create custom header for the download
            custom_header = f"User-Agent: {self._get_random_user_agent()}"
            if header:
                custom_header += f"\n{header}"

            # Start the actual download using HttpDownloader
            print(f"Starting actual download with aria2 to {save_path}/{out}")
            self.http_downloader.start_download(direct_url, save_path, custom_header, out)

            return {
                "status": "downloading",
                "message": f"Started downloading {self.filename} from 1fichier",
                "time_to_get_link": f"{elapsed:.2f} seconds"
            }

        except Exception as e:
            print(f"Error in 1fichier download process: {str(e)}")
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
