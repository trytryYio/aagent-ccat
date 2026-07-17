import os
import requests
import csv
from PIL import Image
from io import BytesIO

# 针对特定商品 ID 重新下载并转换其高清图片
def update_image():
    csv_path = r"d:\Trae CN Work\Rag-Agent\rag\data\products.csv"
    images_dir = r"d:\Trae CN Work\Rag-Agent\rag\data\images"
    target_id = "lining_001"
    
    # 1. Read CSV to get the URL
    image_url = None
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['product_id'] == target_id:
                image_url = row['image_url']
                break
    
    if not image_url:
        print(f"Error: Could not find {target_id} in {csv_path}")
        return

    print(f"Downloading image from: {image_url}")
    
    try:
        # 2. Download the image
        response = requests.get(image_url, timeout=15)
        response.raise_for_status()
        
        # 3. Open and convert to JPG
        # Some AVIF files might require pillow-heif, let's see if standard Pillow handles it
        img = Image.open(BytesIO(response.content))
        
        # Convert to RGB if necessary (AVIF/PNG often have alpha channel)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        target_path = os.path.join(images_dir, f"{target_id}.jpg")
        img.save(target_path, "JPEG", quality=95)
        print(f"Successfully updated {target_path}")
        
    except Exception as e:
        print(f"Failed to update image: {e}")
        print("Note: If the error is about AVIF format, we might need pillow-heif.")

if __name__ == "__main__":
    update_image()
