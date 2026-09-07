"""
Retry handler utility for handling API rate limits and transient errors.
Provides exponential backoff retry logic for Groq API and other services.
"""

import time
import random
import re
from datetime import datetime
from threading import Lock

# Request throttling to prevent rate limits
_request_times = {}
_request_lock = Lock()
MIN_REQUEST_INTERVAL = 4.0  # Increased from 3.0 to 4.0 seconds between requests


def extract_wait_time(error_text: str) -> int:
    """
    Extract wait time in seconds from error message.
    Handles formats like "41m20s" or "29 seconds" or "1800" (seconds)
    """
    try:
        # Look for format like "41m20s" or "41m 20s"
        match = re.search(r'(\d+)m(?:in)?(?:utes?)?[\s]*(\d+)s(?:ec)?(?:onds?)?', error_text, re.IGNORECASE)
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            return minutes * 60 + seconds
        
        # Look for just minutes: "30m" or "30 minutes"
        match = re.search(r'(\d+)\s*m(?:in)?(?:utes?)?', error_text, re.IGNORECASE)
        if match:
            return int(match.group(1)) * 60
        
        # Look for just seconds: "1800" or "30 seconds"
        match = re.search(r'(\d+)\s*s(?:ec)?(?:onds?)?', error_text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        
        # Default to 60 seconds if we can't parse
        return 60
    except:
        return 60


def throttle_request(api_name="groq"):
    """Add throttling between API requests to prevent rate limits."""
    global _request_times
    
    with _request_lock:
        now = datetime.now()
        last_request = _request_times.get(api_name)
        
        if last_request:
            elapsed = (now - last_request).total_seconds()
            if elapsed < MIN_REQUEST_INTERVAL:
                wait_time = MIN_REQUEST_INTERVAL - elapsed
                print(f"[THROTTLE] Waiting {wait_time:.2f}s before next {api_name} request")
                time.sleep(wait_time)
        
        _request_times[api_name] = datetime.now()


def retry_with_backoff(func, max_retries=5, base_delay=2):
    """
    Retry a function with exponential backoff for rate limit errors.
    Extracts actual wait times from error messages when available.
    
    Args:
        func: The function to retry (should be callable with no args)
        max_retries: Maximum number of retry attempts (default 5)
        base_delay: Initial delay in seconds (will double on each retry)
    
    Returns:
        The result of the function call
    
    Raises:
        The last exception if all retries are exhausted
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            error_text = str(e).lower()
            last_exception = e
            
            # Check if it's a rate limit or quota error
            is_quota_limit = (
                "quota" in error_text 
                or "monthly limit" in error_text 
                or "usage limit" in error_text
            )
            is_rate_limit = (
                "rate_limit" in error_text 
                or "rate limit" in error_text 
                or "429" in error_text
                or "too many requests" in error_text
            )
            
            if not (is_rate_limit or is_quota_limit):
                # Not a rate limit/quota error, raise immediately
                raise
            
            if attempt < max_retries - 1:
                # Try to extract actual wait time from error message
                wait_time = extract_wait_time(str(e))
                
                # If we couldn't extract a specific wait time, use exponential backoff
                if wait_time == 60 and attempt > 0:
                    wait_time = base_delay * (2 ** attempt) + random.uniform(0, 2)
                
                error_type = "quota limit" if is_quota_limit else "rate limit"
                print(f"[RETRY] {error_type.upper()} hit. Waiting {wait_time:.0f}s before retry (attempt {attempt + 1}/{max_retries})...")
                print(f"[RETRY] Error details: {error_text[:200]}")
                
                time.sleep(wait_time)
            else:
                print(f"[ERROR] Max retries ({max_retries}) exhausted.")
                raise
    
    # This should not be reached, but just in case
    if last_exception:
        raise last_exception


def run_with_retry_and_throttle(func, api_name="groq", max_retries=5, base_delay=2):
    """
    Run a function with both throttling and retry logic.
    
    Args:
        func: The function to run
        api_name: Name of the API being called (for throttling tracking)
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds for backoff
    
    Returns:
        The result of the function call
    """
    def throttled_func():
        throttle_request(api_name)
        return func()
    
    return retry_with_backoff(throttled_func, max_retries, base_delay)
