
from ytmusicapi import YTMusic
import json

def main():
    yt = YTMusic()
    # Attempt to use the same logic as the app, though we might not have the auth file handy 
    # or we can just use unauthenticated initially as get_charts usually is public.
    # If the app insists on auth, we might need to point to the auth file.
    
    try:
        charts = yt.get_charts(country='US')
        print("Keys in charts:", charts.keys())
        
        if 'videos' in charts:
            print(f"Type of charts['videos']: {type(charts['videos'])}")
            if isinstance(charts['videos'], list):
                print("charts['videos'] is a LIST. First item:", charts['videos'][0] if charts['videos'] else "Empty list")
            elif isinstance(charts['videos'], dict):
                print("charts['videos'] is a DICT. Keys:", charts['videos'].keys())
            else:
                print(f"charts['videos'] is {type(charts['videos'])}")
        else:
            print("'videos' key not found in charts")
            
        # Also check songs and trending
        if 'songs' in charts:
             print(f"Type of charts['songs']: {type(charts['songs'])}")
             
        if 'trending' in charts:
             print(f"Type of charts['trending']: {type(charts['trending'])}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
