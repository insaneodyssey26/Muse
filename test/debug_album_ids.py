from ytmusicapi import YTMusic
import json

yt = YTMusic()

print("--- SEARCH: Michael Jackson - 'Thriller' (Album) ---") # hee hee
results = yt.search("Thriller", filter="albums")
if results:
    print(json.dumps(results[0], indent=2))
else:
    print("No results found")

print("\n--- ARTIST: Rick Astley (Albums) ---")

artist = yt.get_artist("UCuAXFkgsw1L7xaCfnd5JJOw")
if 'albums' in artist and 'results' in artist['albums']:
    print(json.dumps(artist['albums']['results'][0], indent=2))
else:
    print("No albums found in artist")
