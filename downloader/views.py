from django.shortcuts import render
from django.http import FileResponse, JsonResponse
from django.core.cache import cache
from django.views.decorators.csrf import csrf_exempt
import os
import uuid
import threading
from .downloader import download_video, is_valid_url

@csrf_exempt
def index(request):
    if request.method == 'POST':
        if request.headers.get('Content-Type') == 'application/json' or request.POST.get('ajax') or request.POST.get('url'):
            try:
                # AJAX request
                import json
                data = json.loads(request.body) if request.headers.get('Content-Type') == 'application/json' else request.POST
                url = data.get('url', '').strip()
                format_type = data.get('format', '')

                if not url:
                    return JsonResponse({'error': 'URL is required'})
                if not is_valid_url(url):
                    return JsonResponse({'error': 'Invalid URL'})
                if format_type not in ['mp4', 'mp3']:
                    return JsonResponse({'error': 'Invalid format selected'})

                progress_id = str(uuid.uuid4())
                cache.set(progress_id, {'status': 'queued'}, 300)

                # Start download in background
                def download_task():
                    try:
                        file_path = download_video(url, format_type, progress_id)
                        cache.set(f"{progress_id}_file", file_path, 3600)  # Store file path for 1 hour
                    except Exception as e:
                        cache.set(progress_id, {'status': 'error', 'error': str(e)}, 300)

                thread = threading.Thread(target=download_task)
                thread.start()

                return JsonResponse({'progress_id': progress_id})
            except Exception as e:
                return JsonResponse({'error': f'Server error: {str(e)}'})
        else:
            # Regular POST, redirect or handle
            return JsonResponse({'error': 'Use AJAX'})

    return render(request, 'downloader/index.html')

def get_progress(request, progress_id):
    progress = cache.get(progress_id, {'status': 'not_found'})
    return JsonResponse(progress)

def download_file(request, progress_id):
    try:
        import logging
        logger = logging.getLogger(__name__)

        file_path = cache.get(f"{progress_id}_file")
        logger.error(f"Download file request for progress_id: {progress_id}")
        logger.error(f"File path from cache: {file_path}")

        if file_path:
            logger.error(f"File path exists: {file_path}")
            if os.path.exists(file_path):
                logger.error(f"File exists on disk: {file_path}")
                try:
                    file_size = os.path.getsize(file_path)
                    logger.error(f"File size: {file_size}")
                    file_handle = open(file_path, 'rb')
                    response = FileResponse(file_handle, as_attachment=True, filename=os.path.basename(file_path))
                    return response
                except Exception as e:
                    logger.error(f"File open error: {e}")
                    return JsonResponse({'error': f'File error: {str(e)}'})
            else:
                logger.error(f"File does not exist: {file_path}")
                # List directory contents for debugging
                dir_path = os.path.dirname(file_path)
                if os.path.exists(dir_path):
                    contents = os.listdir(dir_path)
                    logger.error(f"Directory contents: {contents}")
                return JsonResponse({'error': f'File not found: {file_path}'})
        else:
            logger.error("No file path in cache")
            return JsonResponse({'error': 'File not ready or not found'})
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error in download_file: {e}")
        return JsonResponse({'error': f'Server error: {str(e)}'})
