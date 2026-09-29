"""Pinned local LocateAnything worker. Images cross a private pipe, never a network API."""
import argparse
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import time

REVISION = 'c32291ca5e996f5a7a485845b4f57a233936bba0'
ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
os.environ.setdefault('HF_MODULES_CACHE', str(ROOT / 'models' / 'locate-code'))
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--size', type=int, default=512, choices=[384, 512, 640])
    parser.add_argument('--gen', choices=['fast', 'hybrid', 'slow'], default='hybrid')
    parser.add_argument('--runs', type=int, default=3)
    args = parser.parse_args()
    channel = sys.stdout
    def emit(value):
        channel.write(json.dumps(value, allow_nan=False) + '\n')
        channel.flush()

    # Model libraries may print; reserve stdout exclusively for our protocol.
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from PIL import Image
        from transformers import AutoModel, AutoProcessor, AutoTokenizer, BitsAndBytesConfig
        model_path = Path.home() / '.cache/huggingface/hub/models--nvidia--LocateAnything-3B/snapshots' / REVISION
        if not (model_path / 'model-00001-of-00002.safetensors').is_file():
            emit({'ready': False, 'error': 'Pinned LocateAnything weights are not present in the local cache.'})
            return
        started = time.perf_counter()
        kw = dict(trust_remote_code=True, local_files_only=True)
        tokenizer = AutoTokenizer.from_pretrained(str(model_path), **kw)
        processor = AutoProcessor.from_pretrained(str(model_path), **kw)
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
                                  bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True)
        model = AutoModel.from_pretrained(str(model_path), **kw, torch_dtype=torch.bfloat16,
                                         quantization_config=quant, device_map={'': 0}).eval()

        def infer(image):
            image = image.convert('RGB')
            image.thumbnail((args.size, args.size))
            question = 'Locate all the instances that matches the following description: standing person</c>sitting person.'
            messages = [{'role': 'user', 'content': [{'type': 'image', 'image': image},
                                                    {'type': 'text', 'text': question}]}]
            text = processor.py_apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            images, videos = processor.process_vision_info(messages)
            inputs = processor(text=[text], images=images, videos=videos, return_tensors='pt').to('cuda:0')
            start = time.perf_counter()
            with torch.inference_mode():
                response = model.generate(pixel_values=inputs['pixel_values'].to(torch.bfloat16),
                    input_ids=inputs['input_ids'], attention_mask=inputs['attention_mask'],
                    image_grid_hws=inputs.get('image_grid_hws'), tokenizer=tokenizer,
                    max_new_tokens=512, use_cache=True, generation_mode=args.gen,
                    temperature=0.15, do_sample=True, top_p=0.9, repetition_penalty=1.1, verbose=False)
            answer = response[0] if isinstance(response, tuple) else response
            if not isinstance(answer, str) or len(answer) > 100000:
                raise ValueError('Unexpected LocateAnything response')
            return {'answer': answer, 'analysis_ms': round((time.perf_counter() - start) * 1000, 1)}

        if args.sample:
            reports = [infer(Image.open(args.sample)) for _ in range(args.runs)]
            report = {'engine': 'LocateAnything', 'revision': REVISION, 'size': args.size,
                      'loading_seconds': round(time.perf_counter() - started, 2), 'runs': reports}
            if args.report:
                args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
            emit(report)
            return
        emit({'ready': True, 'engine': 'LocateAnything', 'revision': REVISION})
        for line in sys.stdin:
            request_id = None
            try:
                if len(line) > 8 * 1024 * 1024:
                    raise ValueError('Frame too large')
                request = json.loads(line)
                request_id = request['id']
                data = base64.b64decode(request['jpeg'], validate=True)
                with Image.open(io.BytesIO(data)) as image:
                    result = infer(image)
                emit({'id': request_id, **result})
            except Exception as exc:
                print(f'LocateAnything frame failed: {type(exc).__name__}', file=sys.stderr, flush=True)
                emit({'id': request_id, 'error': 'LocateAnything could not classify this frame.'})


if __name__ == '__main__':
    main()
