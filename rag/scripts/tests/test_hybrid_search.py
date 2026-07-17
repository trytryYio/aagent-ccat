import os
import sys
import logging

# 将项目根目录添加到 PYTHONPATH
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(BASE_DIR)

from rag.embedding import embed_image, embed_text
from rag.hybrid_search import hybrid_search

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_hybrid_search_flow(image_path: str, query_text: str):
    """
    测试混合搜索全链路流程
    """
    if not os.path.exists(image_path):
        logger.error(f"测试图片不存在: {image_path}")
        return

    logger.info(f"--- 开始测试混合搜索 (Hybrid Search) ---")
    logger.info(f"输入图片: {image_path}")
    logger.info(f"输入文本: {query_text}")

    try:
        # 1. 提取图片向量 (CLIP)
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        logger.info("正在提取图片向量 (CLIP)...")
        image_vector = embed_image(image_bytes)

        # 2. 提取文本向量 (BGE-M3)
        logger.info("正在提取文本向量 (BGE-M3)...")
        text_vector = embed_text(query_text)

        if not image_vector or not text_vector:
            logger.error("向量化失败")
            return

        # 3. 执行混合搜索 (RRF 融合)
        logger.info("正在执行 RRF 混合搜索...")
        results = hybrid_search(
            image_embedding=image_vector, text_embedding=text_vector, top_k=5
        )

        # 4. 打印结果
        if not results:
            logger.warning("未找到匹配商品")
        else:
            logger.info(f"混合搜索完成，Top-5 结果如下:")
            print("\n" + "=" * 70)
            print(f"{'排名':<4} | {'RRF得分':<10} | {'来源':<8} | {'商品名称'}")
            print("-" * 70)
            for i, res in enumerate(results):
                print(f"{i+1:<4} | {res.score:.6f} | {res.source:<8} | {res.name}")
            print("=" * 70 + "\n")

    except Exception as e:
        logger.error(f"测试过程中发生错误: {str(e)}")


if __name__ == "__main__":
    # 使用测试图片和模拟文本进行测试
    test_img = os.path.join(BASE_DIR, "rag", "data", "test-images", "lining01.webp")
    test_text = "李宁羽毛球鞋 白色 专业比赛级"

    test_hybrid_search_flow(test_img, test_text)
