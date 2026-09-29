"""Run from Windows or Linux: python run.py [--backend yoloe|demo|...]."""

import argparse
import logging
import os
import socket
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for folder in [ROOT / "models", ROOT / "data" / "ultralytics", ROOT / "data" / "matplotlib"]:
    folder.mkdir(parents=True, exist_ok=True)
# Keep all model/cache writes inside the new project, regardless of launch directory.
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "huggingface"))
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / "data" / "ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "data" / "matplotlib"))
os.environ.setdefault("YOLO_AUTOINSTALL", "false")


def main():
    parser = argparse.ArgumentParser(description="4479 Vision — stabilized object detection")
    parser.add_argument("--backend", choices=["hybrid", "yoloe", "grounding-dino", "locateanything", "demo"], default="hybrid")
    parser.add_argument("--model", help="Optional local checkpoint or Hugging Face model ID")
    parser.add_argument("--phrase-model", help="Grounding DINO checkpoint for detailed phrases in hybrid mode")
    parser.add_argument('--pose-model', choices=['lite', 'full', 'heavy'], default='full',
                        help=argparse.SUPPRESS)  # Legacy flag; active standing detection uses YOLOE.
    parser.add_argument("--device", default="auto", help="auto, cpu, or cuda:0")
    parser.add_argument("--quant", choices=["4bit", "8bit", "none"], default="4bit")
    parser.add_argument("--gen", choices=["fast", "hybrid", "slow"], default="hybrid")
    parser.add_argument("--revision", help="Hugging Face commit for the optional grounding backend")
    parser.add_argument("--host", choices=["127.0.0.1", "::1"], default="127.0.0.1")
    parser.add_argument('--lan', action='store_true', help='Share all three websites on the private local network')
    parser.add_argument("--port", type=int, default=4479)
    parser.add_argument("--viewer-port", type=int, default=4480,
                        help="Port for the separate 3D bus website; 0 disables it")
    parser.add_argument('--passenger-port', type=int, default=4481, help='Passenger website port; 0 disables it')
    parser.add_argument('--passenger-tls-cert', type=Path, help='PEM certificate for an additional trusted HTTPS passenger site')
    parser.add_argument('--passenger-tls-key', type=Path, help='Private PEM key for the HTTPS passenger site')
    parser.add_argument('--passenger-https-port', type=int, default=4482, help='Optional HTTPS passenger website port (requires certificate and key)')
    parser.add_argument("--ffmpeg", help="Override the bundled FFmpeg executable")
    args = parser.parse_args()
    tls = bool(args.passenger_tls_cert or args.passenger_tls_key)
    if tls and (not args.passenger_tls_cert or not args.passenger_tls_key):
        parser.error('Provide both --passenger-tls-cert and --passenger-tls-key.')
    if tls:
        if not 1 <= args.passenger_https_port <= 65535:
            parser.error('HTTPS passenger port must be between 1 and 65535.')
        if not args.passenger_tls_cert.is_file() or not args.passenger_tls_key.is_file():
            parser.error('The HTTPS certificate and key must be existing PEM files.')
        args.passenger_tls_cert = args.passenger_tls_cert.resolve()
        args.passenger_tls_key = args.passenger_tls_key.resolve()
        import ssl
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(args.passenger_tls_cert), str(args.passenger_tls_key))
        except (OSError, ssl.SSLError):
            parser.error('The HTTPS certificate and private key could not be loaded. Supply a matching unencrypted PEM key and certificate.')
    if not 1 <= args.port <= 65535 or any(not 0 <= port <= 65535 for port in [args.viewer_port, args.passenger_port]):
        parser.error('Ports must be between 1 and 65535 (secondary websites may be 0).')
    ports = [port for port in [args.port, args.viewer_port, args.passenger_port] if port]
    if tls:
        ports.append(args.passenger_https_port)
    if len(ports) != len(set(ports)):
        parser.error('Each website needs a different port.')
    if (args.viewer_port or args.passenger_port or tls) and args.host == '::1' and not args.lan:
        parser.error('Secondary websites need IPv4 loopback or --lan.')
    host = '0.0.0.0' if args.lan else args.host
    os.chdir(ROOT)
    (ROOT / "models").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from vision.config import Settings
    from vision.api import create_app
    import uvicorn
    defaults = {"hybrid": "yoloe-26m-seg.pt", "yoloe": "yoloe-26m-seg.pt", "grounding-dino": "IDEA-Research/grounding-dino-tiny",
                "locateanything": "nvidia/LocateAnything-3B", "demo": "color-components"}
    settings = Settings(backend=args.backend, model=args.model or defaults[args.backend], device=args.device,
                        phrase_model=args.phrase_model or str(ROOT / 'models' / 'grounding-dino-tiny'),
                        pose_model=str(ROOT / 'models' / f'pose_landmarker_{args.pose_model}.task'),
                        quant=args.quant, generation=args.gen, revision=args.revision, ffmpeg=args.ffmpeg)
    from vision.network import trusted_hosts, local_addresses
    from vision.viewer import create_viewer_app
    from vision.passenger_site import create_passenger_app
    for port in ports:
        with socket.socket(socket.AF_INET6 if host == '::1' else socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, port))
    websites = []
    for name, port, factory in [('Bus viewer', args.viewer_port, create_viewer_app),
                                 ('Passenger app', args.passenger_port, create_passenger_app)]:
        if not port:
            continue
        site = uvicorn.Server(uvicorn.Config(factory(args.port, allowed_hosts=trusted_hosts(args.lan)),
                                             host=host, port=port, access_log=False, proxy_headers=False))
        websites.append(site)
        threading.Thread(target=site.run, name=name, daemon=True).start()
        print(f'{name}: http://localhost:{port}/', flush=True)
    if tls:
        site = uvicorn.Server(uvicorn.Config(create_passenger_app(args.port, allowed_hosts=trusted_hosts(args.lan)),
                                             host=host, port=args.passenger_https_port, access_log=False, proxy_headers=False,
                                             ssl_certfile=str(args.passenger_tls_cert), ssl_keyfile=str(args.passenger_tls_key)))
        websites.append(site)
        threading.Thread(target=site.run, name='Passenger HTTPS', daemon=True).start()
        print(f'Passenger HTTPS: https://localhost:{args.passenger_https_port}/ (certificate must match and be trusted)', flush=True)
    if args.lan:
        for address in local_addresses():
            print(f'LAN: http://{address}:{args.port}/ — passenger http://{address}:{args.passenger_port}/', flush=True)
            if tls:
                print(f'Passenger voice: https://{address}:{args.passenger_https_port}/', flush=True)
    print(f"Detection console: http://localhost:{args.port} — available while the model loads.", flush=True)
    try:
        uvicorn.run(create_app(settings, lan=args.lan, api_port=args.port, viewer_port=args.viewer_port,
                               passenger_port=args.passenger_port), host=host, port=args.port,
                    access_log=False, proxy_headers=False)
    finally:
        for site in websites:
            site.should_exit = True


if __name__ == "__main__":
    main()
