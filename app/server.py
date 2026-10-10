# server.py

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from gevent.pywsgi import WSGIServer
from dotenv import load_dotenv
import os
import traceback
import json
import base64

from config import DEFAULT_CONFIGS
from handle_text import prepare_tts_input_with_context
from tts_handler import generate_speech, generate_speech_stream, get_models_formatted, get_models_openai, get_voices, get_voices_formatted, speed_to_rate
from utils import require_api_key, AUDIO_FORMAT_MIME_TYPES

app = Flask(__name__)
load_dotenv()

API_KEY = os.getenv('API_KEY', DEFAULT_CONFIGS["API_KEY"])
PORT = int(os.getenv('PORT', str(DEFAULT_CONFIGS["PORT"])))

DEFAULT_VOICE = os.getenv('DEFAULT_VOICE', DEFAULT_CONFIGS["DEFAULT_VOICE"])
DEFAULT_RESPONSE_FORMAT = os.getenv('DEFAULT_RESPONSE_FORMAT', DEFAULT_CONFIGS["DEFAULT_RESPONSE_FORMAT"])
DEFAULT_SPEED = float(os.getenv('DEFAULT_SPEED', str(DEFAULT_CONFIGS["DEFAULT_SPEED"])))

# CORS: allow all origins so the public deployment works from any browser client.
CORS(app, resources={r"/*": {"origins": "*"}})

# DEFAULT_MODEL = os.getenv('DEFAULT_MODEL', 'tts-1')

def _audio_response(audio_data: bytes, mime_type: str, download_name=None):
    headers = {
        'Content-Type': mime_type,
        'Content-Length': str(len(audio_data)),
    }
    if download_name:
        headers['Content-Disposition'] = f'attachment; filename="{download_name}"'
    return Response(audio_data, mimetype=mime_type, headers=headers)


def _read_and_cleanup(path: str) -> bytes:
    try:
        with open(path, 'rb') as audio_file:
            return audio_file.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def generate_sse_audio_stream(text, voice, speed):
    """Generator function for SSE streaming with JSON events."""
    try:
        # Generate streaming audio chunks and convert to SSE format
        for chunk in generate_speech_stream(text, voice, speed):
            # Base64 encode the audio chunk
            encoded_audio = base64.b64encode(chunk).decode('utf-8')
            
            # Create SSE event for audio delta
            event_data = {
                "type": "speech.audio.delta",
                "audio": encoded_audio
            }
            
            # Format as SSE event
            yield f"data: {json.dumps(event_data)}\n\n"
        
        # Send completion event (stream end marker; no usage data, Edge gives none)
        completion_event = {
            "type": "speech.audio.done",
        }
        yield f"data: {json.dumps(completion_event)}\n\n"
        
    except Exception as e:
        print(f"Error during SSE streaming: {e}")
        # Send error event
        error_event = {
            "type": "error",
            "error": str(e)
        }
        yield f"data: {json.dumps(error_event)}\n\n"

# OpenAI endpoint format
@app.route('/v1/audio/speech', methods=['POST'])
@app.route('/audio/speech', methods=['POST'])  # Add this line for the alias
@require_api_key
def text_to_speech():
    try:
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "Request body must be valid JSON"}), 400
        if 'input' not in data:
            return jsonify({"error": "Missing 'input' in request body"}), 400

        text = data.get('input')

        text = prepare_tts_input_with_context(text)

        # model = data.get('model', DEFAULT_MODEL)
        voice = data.get('voice', DEFAULT_VOICE)
        response_format = data.get('response_format', DEFAULT_RESPONSE_FORMAT)
        try:
            speed = float(data.get('speed', DEFAULT_SPEED))
        except (TypeError, ValueError):
            return jsonify({"error": "'speed' must be a number between 0.25 and 4.0"}), 400
        try:
            speed_to_rate(speed)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        
        # Check stream format - only "sse" triggers streaming
        stream_format = data.get('stream_format', 'audio')  # 'audio' (default) or 'sse'
        
        mime_type = AUDIO_FORMAT_MIME_TYPES.get(response_format, "audio/mpeg")
        
        if stream_format == 'sse':
            # Return SSE streaming response with JSON events
            def generate_sse():
                yield from generate_sse_audio_stream(text, voice, speed)
            
            return Response(
                generate_sse(),
                mimetype='text/event-stream',
                headers={
                    'Content-Type': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no'  # Disable nginx buffering
                }
            )
        else:
            # Return raw audio data (like OpenAI) - can be piped to ffplay
            output_file_path = generate_speech(text, voice, response_format, speed)
            audio_data = _read_and_cleanup(output_file_path)
            return _audio_response(audio_data, mime_type)
            
    except Exception as e:
        app.logger.error(f"Error in text_to_speech: {e!s}\n{traceback.format_exc()}")
        # Return a 500 error for unhandled exceptions, which is more standard than 400
        return jsonify({"error": "An internal server error occurred", "details": str(e)}), 500

# OpenAI endpoint format
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/v1/models', methods=['GET', 'POST'])
@app.route('/models', methods=['GET', 'POST'])
@app.route('/v1/audio/models', methods=['GET', 'POST'])
@app.route('/audio/models', methods=['GET', 'POST'])
def list_models():
    payload = get_models_openai()
    # Legacy key kept for existing clients that parse "models".
    payload["models"] = get_models_formatted()
    return jsonify(payload)

# OpenAI endpoint format
@app.route('/v1/audio/voices', methods=['GET', 'POST'])
@app.route('/audio/voices', methods=['GET', 'POST'])
def list_voices_formatted():
    return jsonify({"voices": get_voices_formatted()})

@app.route('/v1/voices', methods=['GET', 'POST'])
@app.route('/voices', methods=['GET', 'POST'])
@require_api_key
def list_voices():
    specific_language = None

    if request.method == 'GET':
        data = request.args
    else:
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "Request body must be valid JSON"}), 400
    if data and ('language' in data or 'locale' in data):
        specific_language = data.get('language') if 'language' in data else data.get('locale')

    return jsonify({"voices": get_voices(specific_language)})

@app.route('/v1/voices/all', methods=['GET', 'POST'])
@app.route('/voices/all', methods=['GET', 'POST'])
@require_api_key
def list_all_voices():
    return jsonify({"voices": get_voices('all')})

print(" Edge TTS (Free Azure TTS) Replacement for OpenAI's TTS API")
print(" ")
print(" * Serving OpenAI Edge TTS")
print(f" * Server running on http://localhost:{PORT}")
print(f" * TTS Endpoint: http://localhost:{PORT}/v1/audio/speech")
print(" ")

if __name__ == '__main__':
    http_server = WSGIServer(('0.0.0.0', PORT), app)
    http_server.serve_forever()
