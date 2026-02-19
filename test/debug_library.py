import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'src'))
from api.client import MusicClient
# import json

c = MusicClient()
print("Auth:", c.is_authenticated())
try:
    playlists = c.get_library_playlists()
    print("Playlists count:", len(playlists))
    for p in playlists:
        print(f"ID: {p.get('playlistId')}, Title: {p.get('title')}")
        
    # Also check liked songs response structure
    liked = c.get_liked_songs(limit=1)
    if isinstance(liked, dict):
        print("Liked songs is dict keys:", liked.keys())
    else:
        print("Liked songs is list len:", len(liked))
        
except Exception as e:
    print("Error:", e)
