import json
import csv

def csv_to_json(path: str):
    data = {}
    with open(path, 'r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile, delimiter=',')
        for row in reader:
            key = row.pop('Name')
            data[key] = row

    with open('data/card_ratings.json', 'w', encoding='utf-8') as jsonfile:
        json.dump(data, jsonfile, indent=4, ensure_ascii=False)
    
def get_card_ratings(pack: list):
    with open('data/card_ratings.json', 'r') as jsonfile:
        rating_data = json.load(jsonfile)
        jsonfile.close()
    ratings = []
    for name in pack:
        try:
            ratings.append(rating_data[name]['GIH WR'])
        except KeyError:
            ratings.append('0.0')
    return ratings