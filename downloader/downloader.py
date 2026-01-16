import yt_dlp
import os
import tempfile
import logging
from urllib.parse import urlparse
from django.core.cache import cache

logger = logging.getLogger(__name__)

def is_valid_url(url: str) -> bool:
    """Validate if the URL is a valid HTTP/HTTPS URL."""
    try:
        result = urlparse(url)
        return result.scheme in ('http', 'https') and bool(result.netloc)
    except:
        return False

def _progress_hook(progress_id):
    def hook(d):
        if d['status'] == 'downloading':
            downloaded = d.get('_downloaded_bytes', 0)
            total = d.get('_total_bytes', 1)  # Avoid division by zero
            percent = (downloaded / total) * 100 if total > 0 else 0
            cache.set(progress_id, {
                'status': 'downloading',
                'percent': int(percent),
                'downloaded': downloaded,
                'total': total,
                'speed': d.get('_speed_str', 'N/A'),
                'eta': d.get('_eta_str', 'N/A'),
            }, 300)  # Expire in 5 minutes
        elif d['status'] == 'finished':
            cache.set(progress_id, {
                'status': 'finished',
                'filename': d['filename'],
            }, 300)
        elif d['status'] == 'error':
            cache.set(progress_id, {
                'status': 'error',
                'error': 'Download failed',
            }, 300)
    return hook

def download_video(url: str, format_type: str, progress_id: str = None) -> str:
    """
    Download video or audio from the given URL.

    Args:
        url (str): The URL to download from.
        format_type (str): 'mp4' for video, 'mp3' for audio.
        progress_id (str): Optional ID for progress tracking.

    Returns:
        str: Path to the downloaded file.

    Raises:
        ValueError: If URL is invalid or format is unsupported.
        Exception: For download errors.
    """
    if not is_valid_url(url):
        raise ValueError("Invalid URL provided.")

    if format_type not in ['mp4', 'mp3']:
        raise ValueError("Unsupported format. Use 'mp4' or 'mp3'.")

    # Create a temporary directory for downloads
    temp_dir = tempfile.mkdtemp()

    hooks = []
    if progress_id:
        hooks.append(_progress_hook(progress_id))
        cache.set(progress_id, {'status': 'starting'}, 300)

    if format_type == 'mp4':
        ydl_opts = {
            'format': 'best[ext=mp4]/best[height<=720]/best',
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'progress_hooks': hooks,
            'nocheckcertificate': True,
            'ignoreerrors': False,
        }
    elif format_type == 'mp3':
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'extractaudio': True,
            'audioformat': 'mp3',
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'progress_hooks': hooks,
            'nocheckcertificate': True,
            'ignoreerrors': False,
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # If mp3, the filename will be updated after postprocessing
            if format_type == 'mp3':
                filename = filename.rsplit('.', 1)[0] + '.mp3'

            # Verify file exists and has content
            if not os.path.exists(filename):
                raise Exception("Downloaded file not found")

            file_size = os.path.getsize(filename)
            if file_size == 0:
                os.remove(filename)  # Clean up empty file
                raise Exception("Downloaded file is empty")

            if progress_id:
                cache.set(progress_id, {'status': 'completed', 'filename': filename, 'size': file_size}, 300)
            return filename
    except Exception as e:
        logger.error(f"Download error: {e}")
        if progress_id:
            cache.set(progress_id, {'status': 'error', 'error': str(e)}, 300)
        raise Exception(f"Failed to download: {str(e)}")