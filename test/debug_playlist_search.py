import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'src'))
from api.client import MusicClient
import json

c = MusicClient()
try:
    album_id = "OLAK5uy_lbtOplROxod5hrys1ZKNd0QnmrlhX3OI4"
    print(f"Fetching album details for: {album_id}")
    
    try:
        details = c.get_album(album_id)
        print("--- Album Artists Data ---")
        print(json.dumps(details.get('artists'), indent=2))
            
    except Exception as e:
        print(f"get_album failed: {e}")

    # Search to see what SearchPage gets
    query = "XLUUUNAAA" 
    print(f"\nSearching for: {query}")
    results = c.search(query, filter="albums")
    if results:
        print("--- Search Result (First Album) ---")
        # Print keys to see what's available
        print(f"Keys: {results[0].keys()}")
        print("--- Search Result Artists ---")
        print(json.dumps(results[0].get('artists'), indent=2))
        print("--- Search Result Runs ---") # sometimes in runs?
        print(json.dumps(results[0].get('runs'), indent=2))
    else:
        print("No search results found.")
            
except Exception as e:
    print(e)
