"""docstring"""
import json
import os

def get_cards_for_set(expansion_code: str):
    with open('data/bulk_data.json') as json_data:
        bulk_data = json.load(json_data)
        json_data.close()
    cards = []
    print(f'Found {len(bulk_data)} total unique cards.')
    for dic in bulk_data:
        if dic['set'] == expansion_code:
            cards.append(dic)

    print(f'Found {len(cards)} unique cards in {expansion_code.upper()}.')

    if not os.path.exists(f'data/{expansion_code}'):
        os.mkdir(f'data/{expansion_code}')

    with open(f'data/{expansion_code}/cards.json', 'x', encoding='utf-8') as f:
        json.dump(cards, f)

def get_all_card_names_for_set(expansion_code: str):
    with open(f'data/{expansion_code}/cards.json') as json_data:
        card_data = json.load(json_data)
        json_data.close()
    card_names = []
    for dic in card_data:
        card_names.append(dic['name'])
    return card_names