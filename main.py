import os
from crawler.fetch import update_bulk_data, resource_path
import overlay.display as dp

def main():
    bulk_path = 'bulk_data.json'
    bulk_path = resource_path(bulk_path)
    if not os.path.isfile(bulk_path):
        update_bulk_data()
        
    dp.RatingOverlay()

if __name__ == '__main__':
    main()
