import requests
import json
import cv2
import numpy as np
from io import BytesIO
from PIL import Image

API_PATH = 'https://api.scryfall.com/'

def update_bulk_data():
    """Downloads Scryfall's Bulk Data JSON file for all unique card artworks and dumps it to data/bulk_data.json"""
    response = requests.get(API_PATH + 'bulk-data/unique-artwork')
    response_json = response.json()
    data_response = requests.get(response_json['download_uri'])
    data_response_json = data_response.json()
    with open('data/bulk_data.json', 'w') as f:
        json.dump(data_response_json, f)

def prepare_card_image(name='', id=None):
    if name:
        fname = name.replace(' ', '+')
        response = requests.get(API_PATH + f'cards/named?exact={fname}&format=image')
    if id:
        response = requests.get(API_PATH + f'cards/{id}')
    image = Image.open(BytesIO(response.content))
    # image.save(f'{name.replace(' ', '')}.bmp') if name else image.save(f'{id}.bmp')
    image = image.convert('RGB')
    open_cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    return open_cv_image