import discord
from discord.ext import commands
import os
import re
from config import TOKEN
from io import BytesIO
import aiohttp
import asyncio
import shutil
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# URL API для генерации изображений
API_URL = "http://127.0.0.1:8000/generate"
# URL API для получения статуса генерации
STATUS_API_URL = "http://127.0.0.1:8000/status"
# URL API для генерации изображений по референсу
REF_API_URL = "http://127.0.0.1:8000/generate_by_reference"

# Путь к директории, где будут сохраняться изображения
DATASET_PATH = r"E:\spammer\Myproject\models\cache"

# Создание объекта bot с настройками
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Максимальный размер файла для отправки в Discord
MAX_MESSAGE_SIZE_MB = 8  # Максимальный размер одного сообщения в MB (Discord ограничивает размер до 8 MB)
MAX_FILES_PER_MESSAGE = 10  # Максимум 10 файлов в одном сообщении в Discord

def sanitize_filename(text):
    """
    Очищает строку от неподдерживаемых символов для имени файла.
    - Убирает все символы, кроме букв, цифр, пробелов, и тире.
    - Преобразует пробелы в подчеркивания.
    - Обрезает строку до 50 символов.
    """
    clean = re.sub(r'[^\w\s-]', '', text).strip().replace(' ', '_')
    return clean[:50] if clean else "image"

def get_unique_filename(base_name, extension=".png"):
    """
    Генерирует уникальное имя для файла, добавляя счетчик.
    Проверяет, существует ли файл с таким именем в указанной директории.
    Если файл существует, добавляется номер, пока не найдется уникальное имя.
    """
    n = 1
    while True:
        name = f"{base_name}_{n:02d}"
        full_path = os.path.join(DATASET_PATH, f"{name}{extension}")
        if not os.path.exists(full_path):
            return name
        n += 1

async def check_generation_status(task_id, ctx):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150)) as session:
        try:
            async with session.get(f"{STATUS_API_URL}/{task_id}") as res:
                if res.status != 200:
                    await ctx.send(f"❌ Ошибка при запросе статуса задачи. Статус: {res.status}")
                    return None
                data = await res.json()
                return data.get("status", "unknown")
        except Exception as e:
            await ctx.send(f"❌ Произошла ошибка при проверке статуса: {str(e)}")
            return None


async def generate_image_from_api(prompt, num_images, ctx):
    """
    Генерирует изображение через API на основе текста и возвращает путь к изображению.
    """
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150)) as session:
        try:
            logger.info(f"Отправляю запрос в API: {API_URL}, с параметрами: prompt={prompt}, num_images={num_images}")
            async with session.post(API_URL, json={"prompt": prompt, "num_images": num_images}) as res:
                logger.info(f"Ответ от API: {res.status}")

                # Логируем полный текст ответа от API для диагностики
                response_text = await res.text()
                logger.info(f"Ответ от API: {response_text}")

                if res.status != 200:
                    await ctx.send(f"❌ Ошибка при запросе к API. Статус: {res.status}. Ответ: {response_text}")
                    return None

                try:
                    # Пытаемся разобрать JSON
                    data = await res.json()
                    logger.info(f"Полученные данные от API: {data}")

                    # Проверяем, если получены файлы
                    filenames = data.get("filenames", [])
                    if filenames:
                        logger.info(f"Получены файлы: {filenames}")
                        return filenames
                    else:
                        await ctx.send("❌ Не удалось получить файлы от API.")
                        return None

                except Exception as e:
                    # Если ошибка при обработке ответа (например, неправильный формат данных)
                    await ctx.send(f"❌ Ошибка при обработке ответа от API: {e}. Ответ: {response_text}")
                    logger.error(f"Ошибка при обработке ответа от API: {e}. Ответ: {response_text}")
                    return None

        except Exception as e:
            logger.error(f"Ошибка при запросе к API: {e}")
            await ctx.send(f"❌ Произошла ошибка при связи с API: {str(e)}")
            return None

async def get_generated_files(task_id, ctx):
    """
    Проверка статуса задачи и получение файлов по завершении генерации.
    """
    await ctx.send("🔄 Ожидание завершения генерации...")

    while True:
        status = await check_generation_status(task_id, ctx)
        if status == "completed":
            logger.info(f"Генерация завершена для task_id: {task_id}")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150)) as session:
                try:
                    async with session.get(f"{STATUS_API_URL}/files/{task_id}") as res:
                        logger.info(f"Запрос на получение файлов для task_id: {task_id}, статус ответа: {res.status}")
                        if res.status != 200:
                            await ctx.send(f"❌ Ошибка при получении файлов. Статус: {res.status}")
                            return None
                        data = await res.json()
                        filenames = data.get("filenames", [])
                        logger.info(f"Полученные файлы: {filenames}")
                        return filenames
                except Exception as e:
                    logger.error(f"Ошибка при получении файлов: {e}")
                    await ctx.send(f"❌ Произошла ошибка при получении файлов: {str(e)}")
                    return None
        elif status == "failed":
            await ctx.send("❌ Генерация не удалась. Попробуйте снова.")
            return None
        else:
            await asyncio.sleep(5)

@bot.command()
async def generate(ctx, *, prompt: str):
    """
    Команда для генерации изображений по текстовому запросу.
    - Разбирает запрос для получения количества изображений.
    - Отправляет запрос к API для генерации изображений.
    - Сохраняет изображения и отправляет их пользователю.
    """
    parts = prompt.split()
    try:
        num_images = int(parts[-1])  # Последнее слово - это количество изображений
        prompt = ' '.join(parts[:-1])  # Все остальное - это сам промпт
    except ValueError:
        num_images = 1  # Если количество не указано, генерируем одно изображение

    logger.info(f"Запрос от {ctx.author}: {num_images} изображений по запросу '{prompt}'")

    await ctx.send(f"🔄 Генерация {num_images} изображений по запросу: {prompt}... Пожалуйста, подождите.")

    filenames = await generate_image_from_api(prompt, num_images, ctx)

    if not filenames:
        return

    saved_files = []
    for filename in filenames:
        safe_name = sanitize_filename(filename)
        unique_name = get_unique_filename(safe_name)

        if "_x2" in filename:
            unique_name += "_x2"

        final_image_path = os.path.join(DATASET_PATH, f"{unique_name}.png")
        final_prompt_path = os.path.join(DATASET_PATH, f"{unique_name}.txt")

        logger.info(f"Сохраняю изображение {filename} в {final_image_path}")

        if os.path.exists(filename):
            shutil.move(filename, final_image_path)
            with open(final_prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
            saved_files.append(final_image_path)
        else:
            await ctx.send(f"❌ Файл {filename} не найден. Делаем попытку снова. Пропускаем.")

    chunks = []
    current_chunk = []
    current_chunk_size = 0
    current_file_count = 0

    for file in saved_files:
        current_chunk.append(file)
        current_chunk_size += os.path.getsize(file) / (1024 * 1024)  # Размер в МБ
        current_file_count += 1
        if current_chunk_size >= MAX_MESSAGE_SIZE_MB or current_file_count > MAX_FILES_PER_MESSAGE:
            chunks.append(current_chunk)
            current_chunk = []
            current_chunk_size = 0
            current_file_count = 0

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        files = [discord.File(file_path) for file_path in chunk]
        await ctx.send("Вот ваши изображения:", files=files)

@bot.command()
async def refgen(ctx, prompt: str):
    """
    Команда для генерации изображения по референсному изображению и текстовому запросу.
    - Проверяет, есть ли прикрепленное изображение.
    - Отправляет изображение в API для генерации нового изображения по референсу.
    """
    # Проверка на наличие вложений
    if not ctx.message.attachments:
        await ctx.send("❌ Пожалуйста, прикрепите изображение для генерации.")
        return

    # Получаем первое прикрепленное изображение
    attachment = ctx.message.attachments[0]
    
    # Чтение содержимого изображения
    image_bytes = await attachment.read()

    await ctx.send(f"🔄 Генерация изображения по референсу с запросом: {prompt}.")
    try:
        # Отправляем асинхронный запрос к API для генерации по референсу
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150)) as session:
            data = aiohttp.FormData()
            data.add_field('image', image_bytes, filename='image.png', content_type='image/png')

            async with session.post(REF_API_URL, data=data, params={"prompt": prompt}) as res:
                if res.status != 200:
                    await ctx.send(f"❌ Ошибка при запросе к API. Статус: {res.status}")
                    return

                data = await res.json()
                filename = data.get("filename", None)

                if not filename:
                    await ctx.send("❌ Ошибка: изображение не было возвращено.")
                    return

                await ctx.send(f"✅ Изображение успешно сгенерировано! Ссылка на изображение: {filename}")
                await ctx.send(file=discord.File(filename))

    except Exception as e:
        await ctx.send(f"❌ Произошла ошибка при генерации изображения: {e}")

bot.run(TOKEN)
