import os
import re
import time
import random
import tempfile
import requests
import traceback
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
            print("[FICHIER][ERROR] torpy library is not available. 1fichier downloads will not work.")
            print("[FICHIER][ERROR] Please install torpy with: pip install torpy")
            raise Exception("torpy library is required for 1fichier downloads")

    def _check_service_availability(self):
        """Check if 1fichier service is available before attempting downloads"""
        try:
            print("[FICHIER][INFO] Checking 1fichier service availability...")
            # Use a direct request without Tor to check service availability
            headers = {"User-Agent": self._get_random_user_agent()}
            response = requests.get("https://1fichier.com", headers=headers, timeout=10)

            if response.status_code == 200:
                print("[FICHIER][INFO] 1fichier service is available and responding normally")
                return True
            else:
                print(f"[FICHIER][WARNING] 1fichier service returned unexpected status code: {response.status_code}")
                return False
        except requests.exceptions.ConnectionError as e:
            print(f"[FICHIER][ERROR] Connection error checking 1fichier service: {str(e)}")
            return False
        except requests.exceptions.Timeout as e:
            print(f"[FICHIER][ERROR] Timeout checking 1fichier service: {str(e)}")
            return False
        except Exception as e:
            print(f"[FICHIER][ERROR] Unexpected error checking 1fichier service: {str(e)}")
            traceback.print_exc()
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
            print(f"[FICHIER][INFO] Circuit attempt {attempt_id} started")

            # Set headers to mimic a browser
            user_agent = self._get_random_user_agent()
            print(f"[FICHIER][DEBUG] Circuit {attempt_id} using User-Agent: {user_agent}")

            headers = {
                "User-Agent": user_agent,
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
                                print(f"[FICHIER][WARNING] Circuit {attempt_id}: Rate limit detected, 1fichier is blocking this request")
                                # Try to extract the wait time if available
                                wait_match = re.search(r'You must wait ([0-9]+) minutes', response.text)
                                if wait_match:
                                    wait_time = wait_match.group(1)
                                    print(f"[FICHIER][INFO] Circuit {attempt_id}: Wait time specified: {wait_time} minutes")
                                break  # No need to retry this circuit

                            # Try to extract the filename if not already set
                            local_filename = self.filename
                            if local_filename == "download":
                                filename_match = re.search(r'>Filename :<.*<td class="normal">(.*)</td>', response.text)
                                if filename_match:
                                    local_filename = filename_match.group(1)
                                    print(f"[FICHIER][INFO] Circuit {attempt_id}: Extracted filename: {local_filename}")
                                else:
                                    print(f"[FICHIER][WARNING] Circuit {attempt_id}: Could not extract filename from page")

                            # Check if we can find the download form
                            adz_match = re.search(r'name="adz" value="([^"]+)"', response.text)
                            if not adz_match:
                                print(f"[FICHIER][WARNING] Circuit {attempt_id}: No download form found in the page")
                                # Check for common error messages
                                if "file could not be found" in response.text or "file has been deleted" in response.text:
                                    print(f"[FICHIER][ERROR] Circuit {attempt_id}: File not found or has been deleted")
                                elif "file is password protected" in response.text:
                                    print(f"[FICHIER][ERROR] Circuit {attempt_id}: File is password protected, cannot proceed")
                                break  # No need to retry this circuit

                            adz_value = adz_match.group(1)
                            print(f"[FICHIER][INFO] Circuit {attempt_id}: Found download form, submitting request...")

                            # Submit the download form
                            form_data = {"submit": "Download", "adz": adz_value}
                            print(f"[FICHIER][DEBUG] Circuit {attempt_id}: Submitting form with adz value: {adz_value[:10]}...")
                            download_response = session.post(url, data=form_data, headers=headers, allow_redirects=False, timeout=self.circuit_timeout)
                            print(f"[FICHIER][DEBUG] Circuit {attempt_id}: Form submission response status: {download_response.status_code}")

                            # Check for redirect which contains the direct download link
                            if download_response.status_code in (302, 303) and 'Location' in download_response.headers:
                                direct_url = download_response.headers['Location']
                                elapsed = time.time() - start_time
                                print(f"[FICHIER][SUCCESS] Circuit {attempt_id}: Successfully obtained direct link via redirect in {elapsed:.2f}s")
                                print(f"[FICHIER][INFO] Circuit {attempt_id}: Direct URL: {direct_url[:50]}...")
                                return {
                                    'success': True,
                                    'direct_url': direct_url,
                                    'filename': local_filename,
                                    'user_agent': user_agent
                                }

                            # If no redirect, try to find the download link in the response
                            download_link_match = re.search(r'<a href="(https?://[^"]+)"[^>]*>Click here to download the file</a>', download_response.text)
                            if download_link_match:
                                direct_url = download_link_match.group(1)
                                elapsed = time.time() - start_time
                                print(f"[FICHIER][SUCCESS] Circuit {attempt_id}: Successfully extracted direct link from content in {elapsed:.2f}s")
                                print(f"[FICHIER][INFO] Circuit {attempt_id}: Direct URL: {direct_url[:50]}...")
                                return {
                                    'success': True,
                                    'direct_url': direct_url,
                                    'filename': local_filename,
                                    'user_agent': user_agent
                                }

                            print(f"[FICHIER][ERROR] Circuit {attempt_id}: Failed to extract download link from response")
                            # Try to find error messages in the response
                            if "limit" in download_response.text.lower() or "wait" in download_response.text.lower():
                                print(f"[FICHIER][WARNING] Circuit {attempt_id}: Possible rate limit in response")
                            break  # No need to retry this circuit if we got a response but no link

                except (AssertionError, ConnectionError, TimeoutError) as circuit_error:
                    # These are common Tor circuit errors that can be retried
                    if tor_retry < max_tor_retries - 1:
                        print(f"[FICHIER][WARNING] Circuit {attempt_id}: Tor circuit error (retry {tor_retry+1}/{max_tor_retries}): {str(circuit_error)}")
                        time.sleep(0.5)  # Short delay before retry
                        continue
                    else:
                        print(f"[FICHIER][ERROR] Circuit {attempt_id}: Max Tor retries reached, giving up on this circuit")
                        print(f"[FICHIER][DEBUG] Circuit {attempt_id}: Last error: {str(circuit_error)}")
                        break
                except Exception as other_error:
                    print(f"[FICHIER][ERROR] Circuit {attempt_id}: Unexpected error in Tor circuit: {str(other_error)}")
                    traceback.print_exc()
                    break

                # If we got here without exceptions, no need to retry
                break

            return None  # Return None if all retries failed or no link was found

        except Exception as e:
            print(f"[FICHIER][ERROR] Circuit {attempt_id}: Error in circuit attempt: {str(e)}")
            traceback.print_exc()
            return None

    def _get_direct_link(self, url):
        """Get the direct download link from 1fichier using parallel circuits"""
        print(f"[FICHIER][INFO] Starting process to get direct download link from: {url}")
        self._check_tor_available()

        # Check if 1fichier service is available before attempting
        if not self._check_service_availability():
            print("[FICHIER][WARNING] 1fichier service may be unavailable, but will try anyway")

        # Extract filename from URL if possible
        self.filename = self._extract_filename_from_url(url)
        print(f"[FICHIER][INFO] Attempting to download file: {self.filename}")
        print(f"[FICHIER][INFO] Using Tor to bypass 1fichier download restrictions")

        # Track total attempts and successful/failed circuits
        total_attempts = 0
        successful_circuits = 0
        failed_circuits = 0
        batch_delay = 1  # Initial delay between batches (seconds)
        max_batch_delay = 5  # Maximum delay between batches
        start_time = time.time()

        print(f"[FICHIER][INFO] Will attempt up to {self.max_attempts} Tor circuits with {self.max_parallel_attempts} parallel attempts per batch")

        # Try sequential batches of parallel attempts
        for batch in range((self.max_attempts // self.max_parallel_attempts) + 1):
            # Check if we've exceeded max attempts
            if total_attempts >= self.max_attempts:
                break

            batch_start_time = time.time()
            print(f"[FICHIER][INFO] Starting batch {batch+1} of parallel circuit attempts")

            # Calculate how many attempts to make in this batch
            remaining_attempts = self.max_attempts - total_attempts
            batch_size = min(self.max_parallel_attempts, remaining_attempts)

            if batch_size <= 0:
                print(f"[FICHIER][INFO] No more attempts remaining, stopping")
                break

            print(f"[FICHIER][INFO] Batch {batch+1} will use {batch_size} parallel Tor circuits")

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

                            # Store the user agent from the successful circuit
                            self.session_user_agent = result.get('user_agent')
                            if self.session_user_agent:
                                print(f"[FICHIER][INFO] Storing successful User-Agent for download: {self.session_user_agent[:30]}...")

                            elapsed = time.time() - start_time
                            print(f"[FICHIER][SUCCESS] Successfully found direct link after {total_attempts} total attempts ({elapsed:.2f}s)")
                            print(f"[FICHIER][INFO] Direct URL obtained, ready for download")
                            return result['direct_url']
                        else:
                            failed_circuits += 1
                            print(f"[FICHIER][DEBUG] Circuit {circuit_id} failed to get direct link")
                    except Exception as exc:
                        print(f"[FICHIER][ERROR] Circuit {circuit_id} generated an exception: {exc}")
                        traceback.print_exc()
                        failed_circuits += 1

            batch_elapsed = time.time() - batch_start_time
            print(f"[FICHIER][INFO] Batch {batch+1} completed in {batch_elapsed:.2f}s with {batch_size} circuits")

            # If we've reached the maximum number of attempts, break
            if total_attempts >= self.max_attempts:
                print(f"[FICHIER][WARNING] Reached maximum number of attempts ({self.max_attempts})")
                break

            # Implement exponential backoff between batches
            if batch > 0:  # Don't increase delay after first batch
                batch_delay = min(batch_delay * 1.5, max_batch_delay)  # Exponential backoff with cap

            print(f"[FICHIER][WARNING] All circuits in batch {batch+1} failed, waiting {batch_delay:.1f}s before next batch...")
            time.sleep(batch_delay)

        total_elapsed = time.time() - start_time
        print(f"[FICHIER][ERROR] Failed after {total_attempts} attempts in {total_elapsed:.2f}s")
        print(f"[FICHIER][INFO] Statistics: {failed_circuits} failed circuits, {successful_circuits} successful circuits")
        error_msg = f"Failed to get direct download link after {total_attempts} attempts ({total_elapsed:.2f}s)"
        print(f"[FICHIER][ERROR] {error_msg}")
        raise Exception(error_msg)

    def start_download(self, url, save_path, header=None, out=None):
        """Start downloading a file from 1fichier"""
        self.current_url = url
        self.save_path = save_path
        self.session_user_agent = None  # Initialize user agent from successful circuit

        try:
            start_time = time.time()
            print(f"[FICHIER][INFO] Starting 1fichier download process for URL: {url}")
            print(f"[FICHIER][INFO] Save path: {save_path}")

            # Get the direct download link with parallel circuits
            print(f"[FICHIER][INFO] Attempting to get direct download link via Tor circuits")
            direct_url = self._get_direct_link(url)
            self.direct_url = direct_url

            elapsed = time.time() - start_time
            print(f"[FICHIER][SUCCESS] Successfully obtained direct link in {elapsed:.2f} seconds")

            # Use the HttpDownloader to download the file
            if out is None and self.filename:
                out = self.filename
                print(f"[FICHIER][INFO] Using filename from page: {out}")
            elif out is None:
                out = "download"
                print(f"[FICHIER][WARNING] No filename detected, using default: {out}")
            else:
                print(f"[FICHIER][INFO] Using provided filename: {out}")

            # Create custom header for the download
            # Use the User-Agent from the successful circuit if available
            if self.session_user_agent:
                print(f"[FICHIER][INFO] Using User-Agent from successful Tor circuit for download")
                custom_header = f"User-Agent: {self.session_user_agent}"
            else:
                random_ua = self._get_random_user_agent()
                print(f"[FICHIER][INFO] Using random User-Agent for download: {random_ua[:30]}...")
                custom_header = f"User-Agent: {random_ua}"

            if header:
                print(f"[FICHIER][INFO] Adding additional headers to request")
                custom_header += f"\n{header}"

            # Start the actual download using HttpDownloader
            print(f"[FICHIER][INFO] Starting actual download with aria2 to {save_path}/{out}")
            download_result = self.http_downloader.start_download(direct_url, save_path, custom_header, out)
            print(f"[FICHIER][INFO] Download initiated with aria2c, GID: {download_result.get('gid', 'unknown')}")

            return {
                "status": "downloading",
                "message": f"Started downloading {self.filename} from 1fichier",
                "time_to_get_link": f"{elapsed:.2f} seconds",
                "direct_url_obtained": True,
                "filename": out
            }

        except Exception as e:
            error_message = str(e)
            print(f"[FICHIER][ERROR] Error in 1fichier download process: {error_message}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to start download: {error_message}",
                "error_type": "1fichier_process"
            }

    def pause_download(self):
        """Pause the current download"""
        print(f"[FICHIER][INFO] Pausing download for file: {self.filename}")
        result = self.http_downloader.pause_download()
        if result.get('status') == 'paused':
            print(f"[FICHIER][INFO] Successfully paused download")
        else:
            print(f"[FICHIER][WARNING] Failed to pause download: {result.get('message', 'Unknown error')}")
        return result

    def cancel_download(self):
        """Cancel the current download"""
        print(f"[FICHIER][INFO] Cancelling download for file: {self.filename}")
        result = self.http_downloader.cancel_download()
        if result.get('status') == 'cancelled':
            print(f"[FICHIER][INFO] Successfully cancelled download")
        else:
            print(f"[FICHIER][WARNING] Failed to cancel download: {result.get('message', 'Unknown error')}")
        return result

    def get_download_status(self):
        """Get the status of the current download"""
        status = self.http_downloader.get_download_status()
        if status.get('status') == 'error':
            print(f"[FICHIER][ERROR] Download error: {status.get('message', 'Unknown error')}")
        elif status.get('status') == 'completed':
            print(f"[FICHIER][SUCCESS] Download completed for file: {self.filename}")
        elif status.get('progress'):
            # Only log progress occasionally to avoid log spam
            progress = status.get('progress', 0)
            if int(progress) % 10 == 0:  # Log every 10% progress
                print(f"[FICHIER][INFO] Download progress: {progress}% for file: {self.filename}")
        return status
