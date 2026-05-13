#!/usr/bin/env python3
"""
Quick API Reference for the new /analyze-url endpoint
Usage: python quick_test.py <URL>
"""

import sys
import requests
import json
import time

def analyze_url(url: str, fast: bool = False, host: str = "http://127.0.0.1:8000"):
    """
    Analyze a video from URL and wait for results
    
    Args:
        url: HTTP/HTTPS video URL (or magnet link to test placeholder)
        fast: Skip Dolby Vision deep scan
        host: API base URL
    
    Returns:
        Analysis results or None if failed
    """
    print(f"📹 Analyzing: {url[:80]}...")
    
    # Start analysis
    try:
        response = requests.get(
            f"{host}/analyze-url/",
            params={"url": url, "fast": str(fast).lower()},
            timeout=180
        )
    except requests.exceptions.Timeout:
        print("❌ Timeout connecting to API")
        return None
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return None
    
    if response.status_code == 501:
        data = response.json()
        print(f"⏳ {data['detail'][:100]}...")
        return None
    
    if response.status_code != 200:
        print(f"❌ Error {response.status_code}: {response.json().get('detail')}")
        return None
    
    data = response.json()
    job_id = data.get("job_id")
    if not job_id:
        print(f"❌ No job_id returned: {data}")
        return None
    
    print(f"✅ Job started: {job_id}")
    print(f"   {data.get('note', 'Analyzing...')}")
    
    # Poll for results
    max_wait = 300
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            job_response = requests.get(f"{host}/job/{job_id}", timeout=10)
            job = job_response.json()
            status = job.get("status")
            
            if status == "done":
                results = job.get("results", [])
                if results:
                    print("\n📊 Analysis Complete!")
                    result = results[0]
                    print(f"  File: {result.get('file')}")
                    print(f"  DV Profile: {result.get('dv_profile', 'None')}")
                    print(f"  Resolution: {result.get('resolution', 'N/A')}")
                    print(f"  Bitrate: {result.get('bitrate_mbps', 'N/A')} Mbps")
                    print(f"  TV Score: {result.get('tv_score', 'N/A')}")
                    print(f"  Source: {result.get('source_type', 'N/A')}")
                    return result
                else:
                    print("❌ Job done but no results")
                    return None
            elif status == "error":
                error = job.get("error", "Unknown")
                print(f"❌ Analysis failed: {error}")
                return None
            else:
                current = job.get("current", "")
                progress = job.get("progress", "?/1")
                print(f"⏳ {status}: {progress} - {current[:40]}")
                time.sleep(1.5)
        except Exception as e:
            print(f"❌ Error polling job: {e}")
            return None
    
    print(f"⏱️  Analysis timeout after {max_wait}s")
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python quick_test.py <URL>")
        print("\nExamples:")
        print("  python quick_test.py https://www.w3schools.com/html/mov_bbb.mp4")
        print("  python quick_test.py magnet:?xt=urn:btih:...")
        sys.exit(1)
    
    url = sys.argv[1]
    fast_mode = "--fast" in sys.argv
    
    if fast_mode:
        print("🚀 Using fast mode (skipping DV scan)\n")
    
    result = analyze_url(url, fast=fast_mode)
    sys.exit(0 if result else 1)
