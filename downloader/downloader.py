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

def get_available_formats(url: str, cookies: str = None) -> list:
    """
    Get available formats for a video URL.

    Args:
        url (str): The URL to get formats for.
        cookies (str): Optional cookies string for authentication.

    Returns:
        list: List of available formats with metadata.
    """
    if not is_valid_url(url):
        raise ValueError("Invalid URL provided.")

    try:
        ydl_opts = {'quiet': True}
        if cookies:
            # Save cookies to a temporary file
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(cookies)
                ydl_opts['cookiefile'] = f.name

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Debug logging
            if info is None:
                logger.error(f"extract_info returned None for URL: {url}")
                raise Exception("Failed to extract video information. Video may be unavailable or require authentication.")

            formats_list = info.get('formats')
            if formats_list is None:
                logger.error(f"No formats found in info for URL: {url}")
                raise Exception("No video formats available. Video may be private or unavailable.")

            if not isinstance(formats_list, list):
                logger.error(f"Formats is not a list for URL: {url}, got: {type(formats_list)}")
                raise Exception("Invalid format data received from server.")

            formats = []

            for f in formats_list:
                # Filter out very low quality or problematic formats
                if f.get('filesize') and f.get('filesize') < 1024:  # Skip <1KB files
                    continue

                format_info = {
                    'format_id': f.get('format_id', ''),
                    'ext': f.get('ext', 'unknown'),
                    'resolution': f.get('resolution', 'unknown') if f.get('vcodec') != 'none' else None,
                    'filesize': f.get('filesize'),
                    'filesize_str': f.get('filesize') and f"{f['filesize'] / (1024*1024):.1f}MB" or 'Unknown',
                    'vcodec': f.get('vcodec', 'none'),
                    'acodec': f.get('acodec', 'none'),
                    'format_note': f.get('format_note', ''),
                    'fps': f.get('fps'),
                }

                # Categorize formats and add type prefix to format_id
                if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                    format_info['type'] = 'video+audio'
                    format_info['format_id'] = f"video_audio_{f.get('format_id', '')}"
                    format_info['label'] = f"🎥 {f.get('resolution', 'Unknown')} Video+Audio - {format_info['ext'].upper()}"
                elif f.get('vcodec') != 'none':
                    format_info['type'] = 'video'
                    format_info['format_id'] = f"video_{f.get('format_id', '')}"
                    format_info['label'] = f"🎬 Video {f.get('resolution', 'Unknown')} (with audio) - {format_info['ext'].upper()}"
                elif f.get('acodec') != 'none':
                    format_info['type'] = 'audio'
                    format_info['format_id'] = f"audio_{f.get('format_id', '')}"
                    format_info['label'] = f"🎵 Audio {f.get('abr', 'Unknown')}kbps - MP3"
                else:
                    continue

                formats.append(format_info)

            # Sort by quality prioritizing video+audio, then video, then audio
            def sort_key(x):
                type_priority = {
                    'video+audio': 0,
                    'video': 1,
                    'audio': 2
                }
                # For resolution sorting, extract height from resolution string
                resolution = x.get('resolution', '0p')
                height = int(''.join(filter(str.isdigit, resolution))) if resolution != 'unknown' else 0

                return (
                    type_priority.get(x['type'], 3),
                    height,  # Higher resolution first
                    x.get('filesize') or 0  # Larger files (better quality) first
                )

            formats.sort(key=sort_key, reverse=True)

            if not formats:
                logger.error(f"No valid formats found after filtering for URL: {url}")
                raise Exception("No downloadable formats found. The video may be private, deleted, or unavailable.")

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
            # Don't update cache here, let the main function handle completion
            pass
        elif d['status'] == 'error':
            cache.set(progress_id, {
                'status': 'error',
                'error': 'Download failed',
            }, 300)
    return hook

def download_video(url: str, format_spec: str, progress_id: str = None, cookies: str = None) -> str:
    """
    Download video or audio from the given URL.

    Args:
        url (str): The URL to download from.
        format_spec (str): Format specification (format_id from yt-dlp).
        progress_id (str): Optional ID for progress tracking.
        cookies (str): Optional cookies string for authentication.

    Returns:
        str: Path to the downloaded file.

    Raises:
        ValueError: If URL is invalid.
        Exception: For download errors.
    """
    if not is_valid_url(url):
        raise ValueError("Invalid URL provided.")

    if not format_spec or format_spec == "":
        raise ValueError("Format specification is required.")

    # Create a downloads directory in MEDIA_ROOT
    download_dir = os.path.join(settings.MEDIA_ROOT, 'downloads')
    os.makedirs(download_dir, exist_ok=True)

    hooks = []
    if progress_id:
        hooks.append(_progress_hook(progress_id))
        cache.set(progress_id, {'status': 'starting'}, 300)

    # Determine the best format specification based on user selection
    if format_spec.startswith('video_audio_'):
        # User selected a combined video+audio format
        actual_format = format_spec.replace('video_audio_', '')
        ydl_opts = {
            'format': actual_format,
            'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'progress_hooks': hooks,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'no_warnings': True,
        }
    elif format_spec.startswith('video_'):
        # User selected video-only, merge with best audio
        video_format = format_spec.replace('video_', '')
        ydl_opts = {
            'format': f'{video_format}+bestaudio',
            'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'progress_hooks': hooks,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'no_warnings': True,
        }
    elif format_spec.startswith('audio_'):
        # Audio-only format
        actual_format = format_spec.replace('audio_', '')
        ydl_opts = {
            'format': actual_format,
            'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'progress_hooks': hooks,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'no_warnings': True,
        }
    else:
        # Fallback: try to get best combined format, or merge if necessary
        ydl_opts = {
            'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
            'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'progress_hooks': hooks,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'no_warnings': True,
        }

    if cookies:
        # Save cookies to a temporary file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(cookies)
            ydl_opts['cookiefile'] = f.name

    # Add postprocessing for audio-only formats
    if format_spec.startswith('audio_'):
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    try:
        logger.info(f"Starting download with format: {format_spec}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

            # If audio-only format, the filename will be updated after postprocessing
            if format_spec.startswith('audio_'):
                # Wait a bit for postprocessing to complete
                import time
                time.sleep(1)
                filename = filename.rsplit('.', 1)[0] + '.mp3'

            logger.info(f"Expected filename: {filename}")

            # Verify file exists and has content
            if not os.path.exists(filename):
                # Try to find the file with different extensions
                base_name = filename.rsplit('.', 1)[0]
                for ext in ['.mp4', '.webm', '.m4a', '.mp3']:
                    alt_filename = base_name + ext
                    if os.path.exists(alt_filename):
                        filename = alt_filename
                        break
                else:
                    raise Exception(f"Downloaded file not found. Expected: {filename}")

            file_size = os.path.getsize(filename)
            if file_size == 0:
                os.remove(filename)  # Clean up empty file
                raise Exception("Downloaded file is empty")

            logger.info(f"Download completed: {filename} ({file_size} bytes)")

            if progress_id:
                cache.set(progress_id, {'status': 'completed', 'filename': filename, 'size': file_size}, 3600)
            return filename
    except Exception as e:
        logger.error(f"Download error: {e}")
        if progress_id:
            cache.set(progress_id, {'status': 'error', 'error': str(e)}, 300)
        raise Exception(f"Failed to download: {str(e)}")