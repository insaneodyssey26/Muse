
from ytmusicapi import YTMusic
import os
import json

def verify_auth():
    print("Verifying Authentication...")
    auth_path = 'data/headers_auth.json'
    
    if not os.path.exists(auth_path):
        print("No auth file found.")
        return

    try:
        yt = YTMusic(auth_path)
        print("YTMusic instance created.")
        
        # Try to fetch something that requires auth
        # get_liked_songs usually requires auth to get *your* liked songs
        print("Fetching Liked Songs...")
        try:
            liked = yt.get_liked_songs(limit=5)
            print(f"Liked Songs fetch successful. Item count: {len(liked.get('tracks', [])) if isinstance(liked, dict) else len(liked)}")
            if (isinstance(liked, dict) and liked.get('tracks')) or (isinstance(liked, list) and len(liked) > 0):
                print("Headers appear VALID.")
            else:
                 print("Liked songs empty. Could be valid account with no songs, or invalid headers returning public data?")
        except Exception as e:
            print(f"Failed to fetch Liked Songs: {e}")
            print("Headers might be INVALID or EXPIRED.")

        # Try fetching library playlists
        print("Fetching Library Playlists...")
        try:
            playlists = yt.get_library_playlists()
            print(f"Library Playlists fetched. Count: {len(playlists)}")
        except Exception as e:
            print(f"Failed to fetch Library Playlists: {e}")

    except Exception as e:
        print(f"Failed to initialize YTMusic: {e}")

if __name__ == "__main__":
    verify_auth()
