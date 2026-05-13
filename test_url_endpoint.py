#!/usr/bin/env python3
"""
Test script for the new /analyze-url endpoint.
Tests both HTTP URLs and magnet link placeholder.
"""

import requests
import json
import time
import sys

BASE_URL = "http://127.0.0.1:8000"

def test_health():
    """Test that the server is running"""
    print("🔍 Testing server health...")
    try:
        response = requests.get(f"{BASE_URL}/health/")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Server is healthy")
            print(f"   Disk free: {data['disk_free_gb']} GB")
            return True
    except Exception as e:
        print(f"❌ Server health check failed: {e}")
    return False


def test_magnet_link_placeholder():
    """Test that magnet links show the placeholder message"""
    print("\n🧲 Testing magnet link placeholder...")
    magnet_url = "magnet:?xt=urn:btih:1234567890abcdef1234567890abcdef12345678&dn=test.mp4"
    
    try:
        response = requests.get(
            f"{BASE_URL}/analyze-url/",
            params={"url": magnet_url}
        )
        if response.status_code == 501:
            data = response.json()
            detail = data.get("detail", "")
            if "Magnet link support coming soon" in detail and "magnet:" in detail:
                print(f"✅ Magnet link correctly returns 501 (Not Implemented)")
                print(f"   Message: {detail[:100]}...")
                return True
        else:
            print(f"❌ Magnet link should return 501, got {response.status_code}")
            print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"❌ Magnet link test failed: {e}")
    return False


def test_http_url():
    """Test with a real HTTP video URL"""
    print("\n🌐 Testing HTTP URL endpoint...")
    # Try multiple sample URLs
    test_urls = [
        "https://www.w3schools.com/html/mov_bbb.mp4",  # Standard test video
        "https://www.commondatastorage.googleapis.com/gtv-videos-library/sample/BigBuckBunny.mp4",
    ]
    
    for test_url in test_urls:
        try:
            print(f"   Trying: {test_url}")
            response = requests.get(
                f"{BASE_URL}/analyze-url/",
                params={"url": test_url, "fast": "true"},
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                job_id = data.get("job_id")
                if job_id:
                    print(f"✅ URL analyze endpoint returned job_id: {job_id}")
                    print(f"   Note: {data.get('note', 'N/A')}")
                    return job_id
                else:
                    print(f"❌ No job_id in response: {data}")
                    continue
            else:
                print(f"   Status {response.status_code}: {response.json().get('detail', 'Unknown error')}")
                continue
        except requests.exceptions.Timeout:
            print(f"   Timeout connecting to {test_url}")
            continue
        except Exception as e:
            print(f"   Error with {test_url}: {e}")
            continue
    
    print(f"❌ Could not connect to any test URL")
    return None


def check_job_status(job_id):
    """Poll job status"""
    print(f"\n⏳ Checking job status for {job_id}...")
    max_wait = 60  # 60 seconds max
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            response = requests.get(f"{BASE_URL}/job/{job_id}")
            if response.status_code == 200:
                job = response.json()
                status = job.get("status")
                progress = job.get("progress", "N/A")
                current = job.get("current", "")
                
                print(f"   Status: {status} | Progress: {progress} | Current: {current[:50]}")
                
                if status == "done":
                    results = job.get("results", [])
                    if results:
                        print(f"✅ Job completed! Got {len(results)} result(s)")
                        result = results[0]
                        print(f"\n📊 Analysis Results:")
                        print(f"   File: {result.get('file', 'N/A')}")
                        print(f"   Source Type: {result.get('source_type', 'N/A')}")
                        print(f"   Score: {result.get('score', 'N/A')}")
                        print(f"   DV Profile: {result.get('dv_profile', 'N/A')}")
                        print(f"   Resolution: {result.get('resolution', 'N/A')}")
                        print(f"   Bitrate: {result.get('bitrate_mbps', 'N/A')} Mbps")
                        return True
                    else:
                        print(f"❌ Job done but no results")
                        return False
                elif status == "error":
                    error = job.get("error", "Unknown error")
                    print(f"❌ Job failed: {error}")
                    return False
                    
                time.sleep(2)  # Poll every 2 seconds
            else:
                print(f"❌ Failed to get job status: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Error checking job status: {e}")
            return False
    
    print(f"⏱️  Job did not complete within {max_wait} seconds")
    return False


def test_invalid_url():
    """Test with invalid URL"""
    print("\n🚫 Testing invalid URL handling...")
    
    try:
        # Test with non-URL
        response = requests.get(
            f"{BASE_URL}/analyze-url/",
            params={"url": "not-a-url"}
        )
        if response.status_code == 400:
            data = response.json()
            if "HTTP or HTTPS" in str(data.get("detail", "")):
                print(f"✅ Invalid URL correctly returns 400")
                return True
        
        print(f"❌ Invalid URL did not return expected error. Status: {response.status_code}")
        print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"❌ Invalid URL test failed: {e}")
    return False


def main():
    print("=" * 60)
    print("Testing /analyze-url Endpoint")
    print("=" * 60)
    
    # Check server health first
    if not test_health():
        print("\n❌ Server is not running. Start it with:")
        print("   python -m uvicorn main:app --reload")
        sys.exit(1)
    
    # Run tests
    results = {
        "magnet_placeholder": test_magnet_link_placeholder(),
        "invalid_url": test_invalid_url(),
    }
    
    # Test HTTP URL (takes longer)
    job_id = test_http_url()
    if job_id:
        results["http_url_analysis"] = check_job_status(job_id)
    else:
        results["http_url_analysis"] = False
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(results.values())
    if all_passed:
        print("\n🎉 All tests passed!")
    else:
        print("\n⚠️  Some tests failed")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
