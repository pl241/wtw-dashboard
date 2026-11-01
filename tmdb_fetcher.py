import requests
import sys
import json
import os
import time
import gzip
import urllib.request

try:
    from config import TMDB_API_KEY
except ImportError:
    TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

if not TMDB_API_KEY:
    raise ValueError("Error: TMDB_API_KEY is missing! Check your Github Secrets or local config.py.") 

TMDB_BASE_URL = "https://api.themoviedb.org/3"

RATINGS_FILE = "title.ratings.tsv.gz"
RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
CACHE_FILE = "tmdb_metadata_cache.json"

TARGET_PLATFORMS = {
    "8": "Netflix"
}

def safe_get(url, params=None, retries=3, delay=2):
    """Robust request wrapper to handle network drops and timeouts."""
    for attempt in range(retries):
        try:
            res = requests.get(url, params=params, timeout=15)
            time.sleep(0.08)  # Slightly faster politeness throttle for cached optimization
            return res
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
                continue
            return None
    return None

def download_imdb_ratings_dump():
    """Downloads IMDb's official daily rating dataset if local copy is old/missing (>24 hours)."""
    if not os.path.exists(RATINGS_FILE) or (time.time() - os.path.getmtime(RATINGS_FILE) > 86400):
        print("📥 Downloading fresh official IMDb rating dataset (Updates daily)...")
        try:
            urllib.request.urlretrieve(RATINGS_URL, RATINGS_FILE)
            print("✅ IMDb ratings dataset downloaded successfully.")
        except Exception as e:
            print(f"❌ Failed to download IMDb dataset: {e}")

def load_ratings_into_memory():
    """Loads the entire IMDb dataset into a fast in-memory dictionary."""
    download_imdb_ratings_dump()
    ratings_cache = {}
    
    print("🔄 Loading IMDb ratings into memory...")
    start_time = time.time()
    try:
        with gzip.open(RATINGS_FILE, "rt", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) == 3 and parts[0].startswith("tt"):
                    try:
                        ratings_cache[parts[0]] = (float(parts[1]), int(parts[2]))
                    except ValueError:
                        continue
        print(f"✅ Loaded {len(ratings_cache):,} IMDb ratings into memory in {time.time() - start_time:.2f} seconds.")
    except Exception as e:
        print(f"❌ Error reading ratings file: {e}")
        
    return ratings_cache

def load_metadata_cache():
    """Loads previously saved TMDB-to-IMDb mappings to avoid redundant API calls."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
                print(f"📦 Loaded Metadata Cache: {len(cache)} titles remembered (Zero API calls needed for these).")
                return cache
        except Exception:
            pass
    return {}

def save_metadata_cache(cache):
    """Saves the updated metadata cache to disk."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Failed to save metadata cache: {e}")

def get_imdb_code(tmdb_id, is_tv=False):
    """Fetches the official external IMDb canonical identification code from TMDB."""
    media_type = "tv" if is_tv else "movie"
    url = f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}/external_ids"
    res = safe_get(url, params={"api_key": TMDB_API_KEY})
    if res and res.status_code == 200:
        return res.json().get("imdb_id")
    return None

def get_genre_names(genre_ids):
    """Converts multi-array numerical tokens into human-readable labels."""
    genre_map = {
        28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
        99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
        27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance", 878: "Sci-Fi",
        10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
        10759: "Action & Adventure", 10762: "Kids", 10765: "Sci-Fi & Fantasy",
        10764: "Reality TV"
    }
    return ", ".join([genre_map.get(gid) for gid in genre_ids if gid in genre_map]) or "General"

def export_pure_local_imdb_catalog(base_filename="netflix_hk_hybrid_all"):
    if TMDB_API_KEY == "YOUR_REAL_TMDB_API_KEY_HERE":
        print("❌ ERROR: Set operational TMDB API access token inside code parameters before launching.")
        return

    # Step 1: Load IMDb ratings dataset into RAM
    imdb_ratings_db = load_ratings_into_memory()
    
    # Step 2: Load local metadata cache
    metadata_cache = load_metadata_cache()

    json_file = f"{base_filename}.json"
    unique_assets = {}

    for provider_id, platform_name in TARGET_PLATFORMS.items():
        print(f"\n========================================================")
        print(f"🎬 GATHERING NETFLIX HK ASSETS VIA TMDB DISCOVERY")
        print(f"========================================================")

        # --- SWEEP MOVIES ---
        for endpoint, sort_param in [
            (f"{TMDB_BASE_URL}/discover/movie", "primary_release_date.desc"), 
            (f"{TMDB_BASE_URL}/discover/movie", "popularity.desc"), 
            (f"{TMDB_BASE_URL}/discover/movie", "vote_average.desc")
        ]:
            for page in range(1, 41): 
                params = {"api_key": TMDB_API_KEY, "sort_by": sort_param, "page": str(page), "watch_region": "HK", "with_watch_providers": provider_id, "with_watch_monetization_types": "flatrate"}
                if "vote_average" in sort_param: params["vote_count.gte"] = "15" 
                res = safe_get(endpoint, params=params)
                if res and res.status_code == 200:
                    for m in res.json().get("results", []):
                        unique_assets[f"movie_{m['id']}_{provider_id}"] = {"data": m, "is_tv": False, "platform": platform_name}

        # --- SWEEP TV SHOWS ---
        for sort_param in ["first_air_date.desc", "popularity.desc", "vote_average.desc"]:
            for page in range(1, 41):
                params_std = {"api_key": TMDB_API_KEY, "sort_by": sort_param, "page": str(page), "watch_region": "HK", "with_watch_providers": provider_id, "with_watch_monetization_types": "flatrate"}
                if "vote_average" in sort_param: params_std["vote_count.gte"] = "15"

                res_std = safe_get(f"{TMDB_BASE_URL}/discover/tv", params=params_std)
                if res_std and res_std.status_code == 200:
                    for tv in res_std.json().get("results", []): 
                        unique_assets[f"tv_{tv['id']}_{provider_id}"] = {"data": tv, "is_tv": True, "platform": platform_name}

    raw_pool = list(unique_assets.values())
    processed_titles = []
    
    language_labels = {
        "en": "Hollywood/Global English", "zh": "Chinese Cinema (Mandarin)", "cn": "Hong Kong Cinema (Cantonese)", 
        "ja": "Japanese Cinema / Anime", "ko": "Korean Cinema / K-Film", "th": "Thai Cinema / Series",
        "es": "Spanish / Latin Cinema", "fr": "French / European Cinema", "de": "German / European Cinema", "hi": "Indian / Bollywood"
    }

    print(f"\n2. Processing {len(raw_pool)} catalog items (using smart cache for fast lookups)...")

    new_cache_entries = 0

    for idx, asset in enumerate(raw_pool, 1):
        item = asset["data"]
        is_tv = asset["is_tv"]
        platform_name = asset["platform"]
        tmdb_id = str(item.get("id"))
        
        title = item.get("name") if is_tv else item.get("title")
        release_date = item.get("first_air_date") if is_tv else item.get("release_date")
        if not title: continue
            
        sys.stdout.write(f"\r   Processing {idx}/{len(raw_pool)} -> {title[:30]}...")
        sys.stdout.flush()

        try: year = int(release_date.split("-")[0]) if release_date and "-" in release_date else 0
        except: year = 0
        if year == 0: continue

        orig_lang = item.get("original_language", "")
        region_label = language_labels.get(orig_lang, "International / Global Catalog")
        genres = get_genre_names(item.get("genre_ids", []))
        if is_tv and "Reality TV" not in genres: genres += " (Series)"
        
        poster_path = item.get("poster_path")
        live_poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""

        # --- SMART CACHE CHECK ---
        # If we already have this tmdb_id cached, reuse its IMDb tt_code instantly (0 API calls!)
        if tmdb_id in metadata_cache:
            imdb_code = metadata_cache[tmdb_id]
        else:
            # Cache miss: Fetch from TMDB external_ids and save to cache
            imdb_code = get_imdb_code(tmdb_id, is_tv=is_tv)
            metadata_cache[tmdb_id] = imdb_code or "N/A"
            new_cache_entries += 1

        # Lookup rating instantly from local IMDb RAM dictionary
        true_rating, true_votes = 0.0, 0
        if imdb_code and imdb_code != "N/A" and imdb_code in imdb_ratings_db:
            true_rating, true_votes = imdb_ratings_db[imdb_code]

        imdb_url = f"https://www.imdb.com/title/{imdb_code}/" if imdb_code and imdb_code != "N/A" else "N/A"

        # Bayesian Weighted Score Calculation
        m, C = 350, 6.5
        if true_votes >= m:
            weighted_score = (true_votes / (true_votes + m) * true_rating) + (m / (true_votes + m) * C)
        else:
            weighted_score = true_rating * 0.82 if true_votes > 0 else 0.0

        processed_titles.append({
            "title": title, "year": year, "genres": genres,
            "raw_score": true_rating, "weighted_score": weighted_score,
            "votes": true_votes, "platform": platform_name,
            "region": region_label, "imdb_url": imdb_url, "poster_url": live_poster_url
        })

    # Save updated cache back to disk
    if new_cache_entries > 0:
        save_metadata_cache(metadata_cache)
        print(f"\n💾 Saved {new_cache_entries} new titles to metadata cache.")

    processed_titles.sort(key=lambda x: x["weighted_score"], reverse=True)
    
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(processed_titles, f, indent=4, ensure_ascii=False)
    print(f"\n\n✨ SUCCESS: Compiled {len(processed_titles)} Netflix HK records into '{json_file}'.")

if __name__ == "__main__":
    export_pure_local_imdb_catalog()




