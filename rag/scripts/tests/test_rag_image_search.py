import os
import sys
import logging

# 将项目根目录添加到 PYTHONPATH
# 这样可以确保 from rag.xxx import xxx 正常工作
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(BASE_DIR)

from rag.embedding import embed_image
from rag.image_search import search_by_image

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 端到端测试“以图搜图”功能，验证向量检索的准确度
def test_image_search(image_path: str):
    """
    测试以图搜图功能
    """
    if not os.path.exists(image_path):
        logger.error(f"测试图片不存在: {image_path}")
        return

    logger.info(f"--- 开始测试以图搜图 ---")
    logger.info(f"测试图片: {image_path}")

    # 1. 读取图片并向量化
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        
        logger.info("正在提取图片向量 (CLIP)...")
        image_vector = embed_image(image_bytes)
        
        if not image_vector:
            logger.error("图片向量化失败")
            return
            
        logger.info(f"向量提取成功，维度: {len(image_vector)}")

        # 2. 执行检索
        logger.info("正在 Qdrant 中检索相似商品...")
        results = search_by_image(image_vector, top_k=5)

        # 3. 打印结果
        if not results:
            logger.warning("未找到匹配商品 (可能是阈值设置过高或数据库为空)")
        else:
            logger.info(f"成功找到 {len(results)} 个候选商品:")
            print("\n" + "="*50)
            print(f"{'排名':<4} | {'相似度':<8} | {'商品ID':<10} | {'商品名称'}")
            print("-" * 50)
            for i, res in enumerate(results):
                print(f"{i+1:<4} | {res.score:.4f} | {res.product_id:<10} | {res.name}")
            print("="*50 + "\n")

    except Exception as e:
        logger.error(f"测试过程中发生错误: {str(e)}")

if __name__ == "__main__":
    # 使用刚刚抓取的一张本地图片进行测试
    test_img = os.path.join(BASE_DIR, "rag", "data", "test-images", "lining01.webp")
    test_image_search(test_img)
