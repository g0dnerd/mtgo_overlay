import os
import requests
import json
import cv2
import numpy as np
from io import BytesIO
from PIL import Image
import image_recognition.text_extraction as tx

API_PATH = 'https://api.scryfall.com/'

def update_bulk_data():
    """Downloads Scryfall's Bulk Data JSON file for all unique card artworks and dumps it to data/bulk_data.json"""
    response = requests.get(API_PATH + 'bulk-data/oracle-cards')
    response_json = response.json()
    data_response = requests.get(response_json['download_uri'])
    data_response_json = data_response.json()
    with open('data/bulk_data.json', 'w') as f:
        json.dump(data_response_json, f)

def prepare_card_image(name='', id='', save=False):
    p = is_card_cached(name=name, id=id)
    if not p:
        if name:
            fname = name.replace(' ', '')
            p = f'data/mh3/images/full/{fname}.png'
            response = requests.get(API_PATH + f'cards/named?exact={fname}&format=image') #&version=art_crop
        if id:
            response = requests.get(API_PATH + f'cards/{id}?format=image')
        image = Image.open(BytesIO(response.content))
        image = image.convert('RGB')
        open_cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        if save:
            cv2.imwrite(p, open_cv_image)
    else:
        # print('Card image was already cached.')
        open_cv_image = cv2.imread(p)
    return open_cv_image

def title_image(name='', id='', save=False):
    p = is_card_cached(name=name, id=id, title=True)
    open_cv_image = cv2.imread(p)
    return open_cv_image

def is_card_cached(name='', id='', title=False):
    if name:
        fname = f'{name.replace(' ', '')}.png'
        if not title:
            p = f'data/mh3/images/full/{fname}'
        else:
            p = f'data/mh3/images/{fname}'
    if id:
        fname = f'{id}.png'
        if not title:
            p = f'data/mh3/images/full/{fname}'
        else:
            p = f'data/mh3/images/{fname}'
    if os.path.isfile(p):
        return p
    return ''