import cv2

def enhance_contrast(image):
    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    return enhanced

def apply_threshold(image):
    # Assuming the image is already converted to grayscale
    thresh = cv2.adaptiveThreshold(image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                   cv2.THRESH_BINARY, 11, 2)
    return thresh

def resize_image(image, scale_percent):
    width = int(image.shape[1] * scale_percent / 100)
    height = int(image.shape[0] * scale_percent / 100)
    dim = (width, height)
    resized = cv2.resize(image, dim, interpolation=cv2.INTER_AREA)
    return resized

def detect_card_region(image):
    height = image.shape[0]
    vertical_cutoff = int(height // 1.5)
    top_half = image[:vertical_cutoff]
    enhanced = enhance_contrast(top_half)
    thresh = apply_threshold(enhanced)

    """Assumes image contains the top half of the screen."""
    blurred = cv2.GaussianBlur(thresh, (5, 5), 0)
    edges = cv2.Canny(blurred, 100, 200)

    # Find contours
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_x, min_y = image.shape[1], image.shape[0]
    max_x, max_y = 0, 0

    MIN_DIM = 42000
    MAX_DIM = 60000

    # Filter contours based on aspect ratio and size to detect cards
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        aspect_ratio = w / float(h)
        if 0.7 < aspect_ratio < 1.0:
            card_dims = w*h
            if MIN_DIM < card_dims < MAX_DIM:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x + w)
                max_y = max(max_y, y + h)

    if min_x < max_x and min_y < max_y:
        # Return the bounding rectangle covering all detected card-like contours
        return (min_x, min_y, max_x - min_x, max_y - min_y)
    else:
        return None
    
def crop_image_to_region(image, region):
    x, y, w, h = region
    return image[y:y+h, x:x+w], (x, y)
