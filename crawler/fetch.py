import os
import requests
import json
import cv2
import numpy as np
from io import BytesIO
from PIL import Image
from data.resources import resource_path

API_PATH = 'https://api.scryfall.com/'

def update_bulk_data():
    """Downloads Scryfall's Bulk Data JSON file for all unique card artworks and dumps it to data/bulk_data.json"""
    response = requests.get(API_PATH + 'bulk-data/oracle-cards')
    response_json = response.json()
    data_response = requests.get(response_json['download_uri'])
    data_response_json = data_response.json()
    file_path = 'bulk_data.json'
    file_path = resource_path(file_path)
    with open(file_path, 'w') as f:
        json.dump(data_response_json, f)

def prepare_card_image(name='', id='', save=False):
    p = is_card_cached(name=name, id=id)
    if not p:
        if name:
            fname = name.replace(' ', '')
            p = f'mh3/images/{fname}.png'
            p = resource_path(p)
            response = requests.get(API_PATH + f'cards/named?exact={fname}&format=image') #&version=art_crop
        if id:
            response = requests.get(API_PATH + f'cards/{id}?format=image')
        image = Image.open(BytesIO(response.content))
        image = image.convert('RGB')
        open_cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        if save:
            cv2.imwrite(p, open_cv_image)
    else:
        open_cv_image = cv2.imread(p)
    return open_cv_image

def is_card_cached(name='', id=''):
    if name:
        fname = f'{name.replace(' ', '')}.png'
        p = f'mh3/images/{fname}'
    if id:
        fname = f'{id}.png'
        p = f'mh3/images/{fname}'
    p = resource_path(p)
    if os.path.isfile(p):
        return p
    return ''

def get_card_ratings(pack: list):
    file_path = 'card_ratings.json'
    file_path = resource_path(file_path)
    with open(file_path, 'r') as jsonfile:
        rating_data = json.load(jsonfile)
        jsonfile.close()
    ratings = []
    for name in pack:
        try:
            ratings.append(rating_data[name]['GIH WR'])
        except KeyError:
            ratings.append('0.0')
    return ratings