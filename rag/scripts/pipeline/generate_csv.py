import csv
import os
import random

# 根据本地图片文件夹自动生成 mock 商品元数据 products.csv
def generate_products_csv(image_dir, output_csv):
    """
    根据下载的本地图片生成 products.csv
    """
    if not os.path.exists(image_dir):
        print(f"错误: 找不到图片目录 {image_dir}")
        return

    images = [f for f in os.listdir(image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    
    # 手机品牌和描述模板
    brands = ["iPhone", "Xiaomi", "Huawei", "Samsung", "Oppo", "Vivo", "Pixel"]
    models = ["Pro Max", "Ultra", "Standard", "SE", "Plus", "Fold"]
    descriptions = [
        "搭载最新处理芯片，拍照效果极佳，续航能力强。",
        "轻薄设计，屏幕色彩艳丽，适合影音娱乐。",
        "商务首选，系统流畅稳定，隐私保护出色。",
        "折叠屏黑科技，超大视野，工作娱乐两不误。",
        "极致性价比，国民手机，各方面表现均衡。"
    ]

    fieldnames = ['product_id', 'name', 'price', 'description', 'category', 'image_url']
    
    with open(output_csv, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for i, img_name in enumerate(images):
            brand = random.choice(brands)
            model = random.choice(models)
            writer.writerow({
                'product_id': f"phone_{i+1:03d}",
                'name': f"{brand} {model}",
                'price': float(random.randint(2999, 9999)),
                'description': random.choice(descriptions),
                'category': "数码/手机",
                'image_url': f"local://{img_name}" # 使用 local:// 前缀标记本地文件
            })

    print(f"成功生成 {len(images)} 条商品数据至 {output_csv}")

if __name__ == "__main__":
    generate_products_csv("rag/data/images", "rag/data/products.csv")
