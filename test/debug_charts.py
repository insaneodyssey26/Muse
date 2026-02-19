import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'src'))
from api.client import MusicClient
# import json

c = MusicClient()
print("Auth:", c.is_authenticated())
try:
    charts = c.get_charts(country='US')
    print("Keys:", charts.keys())
    # print(json.dumps(charts, indent=2))
    
    if 'videos' in charts:
        v = charts['videos']
        print("Videos type:", type(v))
        if isinstance(v, list):
             print("Videos len:", len(v))
             if len(v) > 0:
                 item = v[0]
                 print("First video item:", item)
                 if 'playlistId' in item:
                     pid = item['playlistId']
                     print(f"Fetching playlist {pid}...")
                     pl = c.get_playlist(pid)
                     print("Playlist tracks:", len(pl.get('tracks', [])) if pl else "None")
except Exception as e:
    import traceback
    traceback.print_exc()
    print("Error:", e)
