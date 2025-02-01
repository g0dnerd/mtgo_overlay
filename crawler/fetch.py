"""Fetches data from the Scryfall API and processes it."""

from io import BytesIO
import time
import json
import csv
import os
import requests
import cv2
import numpy as np
from PIL import Image
from data.resources import resource_path, log_exception

API_PATH = "https://api.scryfall.com/"


def update_bulk_data():
    """Downloads Scryfall's Bulk Data JSON file for all unique card artworks
    and dumps it to data/bulk_data.json.
    """
    response = requests.get(API_PATH + "bulk-data/unique-artwork", timeout=2)
    response_json = response.json()
    data_response = requests.get(response_json["download_uri"], timeout=2)
    data_response_json = data_response.json()
    file_path = "bulk_data.json"
    file_path = resource_path(file_path)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data_response_json, f)


def get_card_list(expansion):
    bulk_path = resource_path("bulk_data.json")
    with open(bulk_path, "r", encoding="utf-8") as jsonfile:
        bulk_data = json.load(jsonfile)
        jsonfile.close()

    cards = []
    for card in bulk_data:
        if card["set"] == expansion:
            cards.append(card)

    card_path = resource_path(f"{expansion}/cards.json")
    with open(card_path, "w", encoding="utf-8") as f:
        json.dump(cards, f)
    


def get_all_ids_for_set(expansion):
    bulk_path = resource_path("bulk_data.json")
    with open(bulk_path, "r", encoding="utf-8") as jsonfile:
        bulk_data = json.load(jsonfile)
        jsonfile.close()

    set_information = resource_path(f"{expansion}/information.json")
    with open(set_information, "r", encoding="utf-8") as jsonfile:
        set_data = json.load(jsonfile)
        jsonfile.close()

    ids = []
    for card in bulk_data:
        if card["set"] == set_data["expansion_code"]:
            ids.append((card["id"], card["set"], card["name"]))
        else:
            if card["set"] in set_data["sub_sets"].keys():
                try:
                    cn = int(card["collector_number"])
                except ValueError:
                    continue
                if cn in set_data["sub_sets"][card["set"]]:
                    ids.append((card["id"], card["set"], card["name"]))

    card_path = resource_path(f"{expansion}/cards.json")

    with open(card_path, "r", encoding="utf-8") as jsonfile:
        expansion_data = json.load(jsonfile)
        jsonfile.close()

    for card in expansion_data:
        try:
            cn = int(card["collector_number"])
        except ValueError:
            continue
        if cn in set_data["sub_sets"]["reprints"]:
            ids.append((card["id"], card["set"], card["name"]))

    return ids


def cache_cards_by_id(ids, expansion):
    rate_limit_delay = 0.1
    last_request_time = time.time()
    for id_string in ids:
        now = time.time()
        delta = now - last_request_time
        if delta < rate_limit_delay:
            time.sleep(rate_limit_delay - delta)
        last_request_time = time.time()
        fetch_card_image(expansion, id_string[1], id_string=id_string[0])


def fetch_card_image(expansion, subset, name="", id_string=""):
    """Gets a card image from cache and saves its CV2 version."""
    if name:
        fname = name.replace(" ", "")
        url = API_PATH + f"cards/named?exact={fname}&format=image"
    elif id_string:
        fname = id_string
        url = f"{API_PATH}cards/{id_string}?format=image"
    else:
        raise ValueError(
            "Either name or ID string have to be provided when fetching image."
        )

    cached_path = is_card_cached(expansion, subset, fname)
    if cached_path:
        return cached_path

    # Fetch the image
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
    except requests.RequestException as e:
        log_exception(f"Error fetching image: {e}")
        raise

    # Save and return the image path
    p = f"{expansion}/images/{subset}/{fname}.png"
    p = resource_path(p)
    image = Image.open(BytesIO(response.content)).convert("RGB")
    open_cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    cv2.imwrite(p, open_cv_image)
    return p


def get_card_image(expansion, subset, name="", id_string=""):
    save_location = fetch_card_image(expansion, subset, name=name, id_string=id_string)
    return cv2.imread(save_location)


def is_card_cached(expansion, subset, fname):
    """Returns the file path for a card image file if it is cached
    and an empty string otherwise.
    """
    p = f"{expansion}/images/{subset}/{fname}.png"
    p = resource_path(p)
    return p if os.path.isfile(p) else ""


def get_card_ratings(expansion, pack: list):
    """Fetches the card ratings for a pack from the JSON ratings file."""
    file_path = f"{expansion}/card_ratings.json"
    file_path = resource_path(file_path)
    with open(file_path, "r", encoding="utf-8") as jsonfile:
        rating_data = json.load(jsonfile)
        jsonfile.close()
    ratings = []
    for name in pack:
        try:
            ratings.append(rating_data[name]["GIH WR"])
        except KeyError:
            ratings.append("0.0")
    return ratings


def ratings_to_json(expansion):
    """Converts 17Lands rating data from their CSV format to a JSON file."""
    file_path = resource_path(f"{expansion}/card_ratings.csv")
    with open(file_path, "r", encoding="utf-8-sig") as csv_file:
        csv_data = csv.reader(csv_file, delimiter=",", quotechar='"')
        headers = next(csv_data)
        cards_dict = {}
        for row in csv_data:
            name = row[0].strip()
            card_dict = {}
            for key, item in zip(headers[1:], row[1:]):
                item = item.strip()
                card_dict[key] = item
            cards_dict[name] = card_dict

    output_path = resource_path(f"{expansion}/card_ratings.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cards_dict, f, indent=4)


def cache_variants(expansion):
    ids = get_all_ids_for_set(expansion)
    card_variants = {}
    for id_string, subset, name in ids:
        # Strip back faces from MDFC names
        name = name.split(' //', 1)[0]
        if name not in card_variants:
            card = {name: {subset: [id_string]}}
            card_variants.update(card)
        else:
            card_variants[name][subset].append(id_string)

    output_path = resource_path(f"{expansion}/card_variants.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(card_variants, f, indent=4)
