import os
import aiohttp
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import uuid
from config import TOKEN2

API_URL = "http://127.0.0.1:8000/generate"
REF_API_URL = "http://127.0.0.1:8000/generate_by_reference"
DATASET_PATH = r"E:\spammer\MyProject\datasets"  # Путь к папке для сохранения изображений и промптов

if not os.path.exists(DATASET_PATH):
    os.makedirs(DATASET_PATH)

# ===  генерация по txt (txt2img) ===
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = ' '.join(context.args)
    
    if not prompt:
        await update.message.reply_text("Напиши промпт после /generate")
        return

    await update.message.reply_text(f"Генерация по промпту: {prompt}")
    
    try:
        # Запрос к API
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json={"prompt": prompt}) as res:
                if res.status != 200:
                    await update.message.reply_text("Ошибка при генерации изображения.")
                    return
                
                data = await res.json()

        # Проверка, что в ответе есть путь к файлу
        if "filename" not in data:
            await update.message.reply_text("Ошибка: файл не был сгенерирован. Попробуйте снова.")
            return
        
        file_path = data["filename"]

        # Проверка существования файла
        if not os.path.exists(file_path):
            await update.message.reply_text("Ошибка: файл не найден.")
            return
        
        # Генерация уникального имени файла для датасета
        unique_id = uuid.uuid4().hex
        base_filename = f"{prompt.replace(' ', '_')[:30]}_{unique_id}"
        
        image_path = os.path.join(DATASET_PATH, f"{base_filename}.png")
        prompt_path = os.path.join(DATASET_PATH, f"{base_filename}.txt")
        
        # Перемещение сгенерированного изображения в папку для датасета
        os.rename(file_path, image_path)
        
        # Сохранение промпта в текстовый файл
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)

        # Отправка изображения пользователю
        with open(image_path, "rb") as file:
            await update.message.reply_photo(photo=InputFile(file))

        # Сообщение о том, что сохранено в датасет
        await update.message.reply_text(f"Изображение и промпт успешно сохранены в датасет под именем {base_filename}.")

    except aiohttp.ClientError as e:
        await update.message.reply_text(f"Ошибка при запросе к API: {e}")
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка: {e}")

# ===  генерация по референсу (img2img) ===
async def refgen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем наличие изображения и промпта
    if not update.message.photo or not context.args:
        await update.message.reply_text("Пришли промпт и прикрепи изображение после команды /refgen.")
        return

    prompt = ' '.join(context.args)
    await update.message.reply_text(f"🖼 Генерация по референсу: `{prompt}`")

    try:
        # Получаем изображение в наивысшем качестве
        photo_file = await update.message.photo[-1].get_file()
        img_bytes = await photo_file.download_as_bytearray()

        # Отправляем на API
        async with aiohttp.ClientSession() as session:
            files = {'image': ('reference.png', img_bytes, 'image/png')}
            params = {'prompt': prompt}
            async with session.post(REF_API_URL, params=params, files=files) as response:
                if response.status != 200:
                    await update.message.reply_text("❌ Ошибка генерации изображения.")
                    return

                data = await response.json()
                filename = data.get("filename")

                if not filename or not os.path.exists(filename):
                    await update.message.reply_text("⚠️ Не удалось получить изображение.")
                    return

                # Сохраняем в датасет
                unique_id = uuid.uuid4().hex
                base_filename = f"{prompt.replace(' ', '_')[:30]}_{unique_id}"
                image_path = os.path.join(DATASET_PATH, f"{base_filename}.png")
                prompt_path = os.path.join(DATASET_PATH, f"{base_filename}.txt")

                os.rename(filename, image_path)

                with open(prompt_path, "w", encoding="utf-8") as f:
                    f.write(prompt)

                # Отправляем пользователю
                with open(image_path, "rb") as f:
                    await update.message.reply_photo(photo=InputFile(f))

                await update.message.reply_text(f"✅ Сохранено как `{base_filename}`")

    except Exception as e:
        await update.message.reply_text(f"🚨 Ошибка: {e}")

# Создание приложения бота
app = ApplicationBuilder().token(TOKEN2).build()

# Добавление обработчика команды
app.add_handler(CommandHandler("generate", generate))
app.add_handler(CommandHandler("refgen", refgen))

# Запуск бота
app.run_polling()
