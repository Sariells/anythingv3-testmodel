import os
import uuid
import logging
import torch
import ray
import psutil
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from pydantic import BaseModel
from diffusers import StableDiffusionPipeline, StableDiffusionImg2ImgPipeline
from PIL import Image
import numpy as np
import tempfile
import asyncio
import cv2
import io
from torch.cuda.amp import autocast  # Для ускорения работы с пониженной точностью

# Инициализация Ray
logger = logging.getLogger("FastAPI")
logging.basicConfig(level=logging.INFO)

logger.info("Инициализация Ray...")
ray.init(ignore_reinit_error=True, logging_level=logging.INFO)
logger.info("Ray инициализирован ✅")

# Инициализация FastAPI
app = FastAPI()

# Параметры
MODEL_PATH = r"E:\spammer\Myproject\stable-diffusion-webui\models\converted_anythingv3"
CACHE_DIR = r"E:\spammer\Myproject\models\cache"
MAX_SIZE = (512, 512)

# Создание кэш директории
os.makedirs(CACHE_DIR, exist_ok=True)
logger.info(f"Кэш директория создана: {CACHE_DIR}")

# Загрузка моделей
logger.info("Загрузка моделей...")

try:
    txt2img_pipe = StableDiffusionPipeline.from_pretrained(
        MODEL_PATH,
        torch_dtype=torch.float16,
        safety_checker=None,
    ).to("cuda")
    
    img2img_pipe = StableDiffusionImg2ImgPipeline.from_pretrained(
        MODEL_PATH,
        torch_dtype=torch.float16,
        safety_checker=None,
    ).to("cuda")

    logger.info("Модели загружены ✅")

    # Попытка активировать xformers для оптимизации памяти
    try:
        txt2img_pipe.enable_xformers_memory_efficient_attention()
        img2img_pipe.enable_xformers_memory_efficient_attention()
        logger.info("✅ xformers активирован.")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось активировать xformers: {e}")
except Exception as e:
    logger.error(f"Ошибка при загрузке моделей: {e}")
    raise

# Очистка памяти GPU
def clear_gpu_memory():
    torch.cuda.empty_cache()
    logger.info("GPU память очищена.")

# Функция для увеличения изображения
async def upscale_image(input_path, scale=2):
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    try:
        image = Image.open(input_path).convert("RGB")
        image_np = np.array(image)
        height, width = image_np.shape[:2]
        new_size = (width * scale, height * scale)

        upscaled_np = cv2.resize(image_np, new_size, interpolation=cv2.INTER_CUBIC)
        upscaled_image = Image.fromarray(upscaled_np)

        output_path = input_path.replace(".png", f"_x{scale}.png")
        upscaled_image.save(output_path)
        logger.info(f"Изображение увеличено и сохранено: {output_path}")
        return output_path
    except Exception as e:
        logger.error(f"Ошибка при увеличении изображения: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка при увеличении изображения: {e}")

# Функция для проверки системных ресурсов
def check_system_resources():
    cpu_usage = psutil.cpu_percent(interval=1)
    ram_usage = psutil.virtual_memory().percent
    logger.info(f"CPU: {cpu_usage}%, RAM: {ram_usage}%")
    
    if cpu_usage > 90 or ram_usage > 90:
        logger.warning("Системные ресурсы перегружены.")
        return False
    return True


# Асинхронная генерация изображения
async def generate_image(prompt: str, is_reference: bool = False, image: Image.Image = None, use_ray: bool = False):
    try:
        clear_gpu_memory()

        # Проверка ресурсов перед выполнением
        if not check_system_resources():
            raise HTTPException(status_code=503, detail="Системные ресурсы перегружены, попробуйте позже")

        logger.info(f"Начинаем генерацию изображения с prompt: {prompt}")
        
        # Если мы используем img2img (с референсным изображением)
        if is_reference:
            if image is None:
                raise ValueError("Reference image is required.")
            logger.info(f"Запуск img2img генерации с prompt: {prompt}")

            # Используем ray, если указано
            if use_ray:
                result = await ray.get(generate_img2img_task.remote(prompt, image))
            else:
                result = img2img_pipe(prompt=prompt, image=image, strength=0.4, guidance_scale=8.5, num_inference_steps=40).images[0]

        # Если используем текстовое описание (txt2img)
        else:
            negative_prompt = (
                "low quality, blurry, jpeg artifacts, bad anatomy, extra limbs, deformed hands, "
                "missing fingers, extra fingers, mutated, poorly drawn, lowres, watermark, text, "
                "logo, signature, badly drawn hands, disfigured hands, bad hands, missing fingers"
            )
            logger.info(f"Запуск txt2img генерации с prompt: {prompt}")

            # Используем ray, если указано
            if use_ray:
                result = await ray.get(generate_txt2img_task.remote(prompt, negative_prompt))
            else:
                result = txt2img_pipe(prompt=prompt, negative_prompt=negative_prompt, num_inference_steps=35, guidance_scale=9.0).images[0]

        # Сохраняем изображение в кэш
        filename = f"{'ref' if is_reference else 'gen'}_{uuid.uuid4().hex}.png"
        raw_path = os.path.join(CACHE_DIR, filename)
        result.save(raw_path)

        logger.info(f"Изображение сохранено как: {raw_path}")

        # Для увеличения изображения по пути после сохранения
        return await upscale_image(raw_path)

    except Exception as e:
        logger.error(f"Ошибка при генерации изображения: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка при генерации изображения: {e}")

# Модель для приема данных от клиента
class PromptRequest(BaseModel):
    prompt: str
    num_images: int = 1  # По умолчанию 1 изображение

# Структура для хранения статуса задачи
tasks_status = {}

# Основная функция для генерации изображений
@app.post("/generate")
async def generate_images(data: PromptRequest):
    try:
        prompt = data.prompt.strip()

        # Ограничение на количество одновременно выполняемых задач
        max_tasks = 2  # Максимум 2 параллельные задачи
        tasks = [generate_image(prompt=prompt, is_reference=False) for _ in range(min(data.num_images, max_tasks))]

        # Ожидание всех задач
        task_ids = [str(uuid.uuid4()) for _ in tasks]
        for task_id, task in zip(task_ids, tasks):
            tasks_status[task_id] = "in_progress"
        
        upscaled_images = await asyncio.gather(*tasks)

        for task_id in task_ids:
            tasks_status[task_id] = "completed"

        logger.info(f"Генерация {data.num_images} изображений завершена.")
        return {"filenames": upscaled_images}
    except Exception as e:
        logger.error(f"Ошибка при генерации изображений: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка при генерации изображений: {e}")

# Эндпоинт для получения статуса задачи
@app.get("/status")
async def get_status(task_id: str):
    """
    Возвращает статус задачи по task_id
    """
    if task_id not in tasks_status:
        raise HTTPException(status_code=404, detail="Task not found")
    
    status = tasks_status[task_id]
    return {"task_id": task_id, "status": status}

# Функция для генерации изображения по референсному изображению
async def generate_reference_image(prompt: str, image_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        logger.info("Генерация изображения по референсному изображению...")
        return await generate_image(prompt=prompt, is_reference=True, image=image)
    except Exception as e:
        logger.error(f"Ошибка при генерации изображения по референсному изображению: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка при генерации изображения по референсному изображению: {e}")

# Эндпоинт для генерации изображения с референсным изображением
@app.post("/generate_by_reference")
async def generate_by_reference_image(
    prompt: str = Form(...),  # Формат для текстового запроса
    image: UploadFile = File(...),  # Формат для загрузки изображения
):
    try:
        logger.info("Получен референс для генерации.")
        
        # Проверим, что файл действительно загружен
        image_bytes = await image.read()
        logger.info(f"Получено изображение с размером {len(image_bytes)} байт.")

        # Генерация изображения по референсному изображению
        filename = await generate_reference_image(prompt=prompt, image_bytes=image_bytes)

        logger.info(f"Референсное изображение сгенерировано и сохранено: {filename}")
        return {"filename": filename}
    
    except Exception as e:
        logger.error(f"Ошибка при обработке референсного изображения: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при обработке изображения")

# Функции для выполнения через Ray (для асинхронных задач)
@ray.remote
def generate_img2img_task(prompt, image):
    return img2img_pipe(prompt=prompt, image=image, strength=0.4, guidance_scale=8.5, num_inference_steps=40).images[0]

@ray.remote
def generate_txt2img_task(prompt, negative_prompt):
    return txt2img_pipe(prompt=prompt, negative_prompt=negative_prompt, num_inference_steps=35, guidance_scale=9.0).images[0]

if __name__ == "__main__":
    import uvicorn
    logger.info("Запуск сервера FastAPI...")
    uvicorn.run("server:app", host="127.0.0.1", port=8000)
    logger.info("Сервер запущен на http://127.0.0.1:8000")  
