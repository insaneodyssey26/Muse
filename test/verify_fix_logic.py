
from ytmusicapi import YTMusic
import json

def verify_logic():
    yt = YTMusic()
    print("Fetching charts...")
    try:
        charts = yt.get_charts(country='US')
    except Exception as e:
        print(f"Failed to get charts: {e}")
        return

    print(f"Keys in charts: {list(charts.keys())}")
    
    # Simulate the logic added to explore.py (with error handling)
    if 'videos' in charts and isinstance(charts['videos'], list) and len(charts['videos']) > 0:
        print("Detected 'videos' as list.")
        try:
            trending_playlist = charts['videos'][0]
            if 'playlistId' in trending_playlist:
                playlist_id = trending_playlist['playlistId']
                print(f"Fetching playlist: {playlist_id}")
                
                # Fetch the actual tracks
                playlist_data = yt.get_playlist(playlist_id)
                
                if playlist_data and 'tracks' in playlist_data:
                    print(f"Playlist fetched. Track count: {len(playlist_data['tracks'])}")
                    charts['videos'] = {'items': playlist_data['tracks']}
                    print("Transformation applied.")
                else:
                    print("Playlist fetched but no tracks or empty.")
                    del charts['videos']
            else:
                print("No playlistId found.")
        except Exception as e:
            print(f"Caught expected exception during fetch: {e}")
            if 'videos' in charts:
                del charts['videos']
            print("Successfully handled exception by removing 'videos' key.")
    elif 'videos' in charts and not isinstance(charts['videos'], dict):
        print("Unexpected structure for 'videos', removing.")
        del charts['videos']

    # Verify that we can access charts['videos'] safely or it's gone
    if 'videos' in charts:
        if 'items' in charts['videos']:
             print(f"SUCCESS: charts['videos']['items'] exists. Count: {len(charts['videos']['items'])}")
        else:
             print("FAILURE: 'videos' exists but has no 'items' (and wasn't removed).")
    else:
        print("SUCCESS: 'videos' key was removed (graceful failure). UI will skip it.")

if __name__ == "__main__":
    verify_logic()
