import yt_dlp
import os
import tempfile
import logging
from urllib.parse import urlparse
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger(__name__)

def is_valid_url(url: str) -> bool:
    """Validate if the URL is a valid HTTP/HTTPS URL."""
    try:
        result = urlparse(url)
        return result.scheme in ('http', 'https') and bool(result.netloc)
    except:
        return False

def get_available_formats(url: str) -> list:
    """
    Get available formats for a video URL.

    Args:
        url (str): The URL to get formats for.

    Returns:
        list: List of available formats with metadata.
    """
    if not is_valid_url(url):
        raise ValueError("Invalid URL provided.")

    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []

            for f in info.get('formats', []):
                # Filter out very low quality or problematic formats
                if f.get('filesize') and f.get('filesize') < 1024:  # Skip <1KB files
                    continue

                format_info = {
                    'format_id': f.get('format_id', ''),
                    'ext': f.get('ext', 'unknown'),
                    'resolution': f.get('resolution', 'unknown') if f.get('vcodec') != 'none' else None,
                    'filesize': f.get('filesize'),
                    'filesize_str': f.get('filesize', 0) and f"{f['filesize'] / (1024*1024):.1f}MB" or 'Unknown',
                    'vcodec': f.get('vcodec', 'none'),
                    'acodec': f.get('acodec', 'none'),
                    'format_note': f.get('format_note', ''),
                    'fps': f.get('fps'),
                }

                # Categorize formats
                if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                    format_info['type'] = 'video+audio'
                    format_info['label'] = f"{f.get('resolution', 'Unknown')} - {format_info['ext'].upper()}"
                elif f.get('vcodec') != 'none':
                    format_info['type'] = 'video'
                    format_info['label'] = f"Video {f.get('resolution', 'Unknown')} - {format_info['ext'].upper()}"
                elif f.get('acodec') != 'none':
                    format_info['type'] = 'audio'
                    format_info['label'] = f"Audio {f.get('abr', 'Unknown')}kbps - {format_info['ext'].upper()}"
                else:
                    continue

                formats.append(format_info)

            # Sort by quality (video first, then audio)
            formats.sort(key=lambda x: (
                0 if x['type'] == 'video+audio' else 1 if x['type'] == 'video' else 2,
                x.get('filesize') or 0
            ), reverse=True)

            return formats[:20]  # Limit to top 20 formats

    except Exception as e:
        logger.error(f"Error getting formats: {e}")
        raise Exception(f"Failed to get video formats: {str(e)}")

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

def download_video(url: str, format_spec: str, progress_id: str = None) -> str:
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

    # Create a downloads directory in MEDIA_ROOT
    download_dir = os.path.join(settings.MEDIA_ROOT, 'downloads')
    os.makedirs(download_dir, exist_ok=True)
    temp_dir = download_dir

    hooks = []
    if progress_id:
        hooks.append(_progress_hook(progress_id))
        cache.set(progress_id, {'status': 'starting'}, 300)

    # Use the specific format_id provided
    ydl_opts = {
        'format': format_spec,
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
        'quiet': True,
        'progress_hooks': hooks,
        'nocheckcertificate': True,
        'ignoreerrors': False,
    }

    # Add postprocessing for audio formats
    if 'audio' in format_spec.lower() or format_spec.endswith('mp3') or format_spec.endswith('m4a'):
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

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