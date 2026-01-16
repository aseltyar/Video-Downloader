from django.shortcuts import render, get_object_or_404
from django.http import FileResponse, HttpResponseBadRequest, JsonResponse
from django.conf import settings
from django.core.cache import cache
import os
import uuid
import threading
from .downloader import download_video, is_valid_url

def index(request):
    if request.method == 'POST':
        if request.headers.get('Content-Type') == 'application/json' or request.POST.get('ajax'):
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
    file_path = cache.get(f"{progress_id}_file")
    if file_path and os.path.exists(file_path):
        response = FileResponse(open(file_path, 'rb'), as_attachment=True, filename=os.path.basename(file_path))
        return response
    return JsonResponse({'error': 'File not ready or not found'})
