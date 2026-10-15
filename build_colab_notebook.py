import ast
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
cells = []


def markdown(text):
    cells.append(
        {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}
    )


def code(text):
    cells.append(
        {
            "cell_type": "code",
            "metadata": {},
            "source": text.splitlines(keepends=True),
            "outputs": [],
            "execution_count": None,
        }
    )


markdown("""# تبدیل ویس‌های آرشیو با Whisper روی GPU
فقط تبدیل صوت اینجا انجام می‌شود. اصلاح با ChatGPT و اکسل روی کامپیوتر شما ادامه پیدا می‌کند.

1. از **Runtime → Change runtime type** گزینه **T4 GPU** یا GPU موجود را انتخاب کنید.
2. سلول‌ها را به ترتیب اجرا کنید و `colab_voices.zip` را آپلود کنید.
3. فایل `whisper_colab_results.zip` را دانلود کنید و مسیرش را به برنامه روی کامپیوتر بدهید.

آپلود بسته، ویس‌ها را به محیط حساب Google شما منتقل می‌کند. این دفترچه کلید یا ورود ChatGPT نمی‌خواهد.
""")
code(
    '%pip -q install faster-whisper==1.2.1 av==16.0.1 nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"\n'
)
code("""import ctypes, glob, os, site
from pathlib import Path
library_dirs = [p for base in site.getsitepackages() for p in glob.glob(base + '/nvidia/*/lib')]
os.environ['LD_LIBRARY_PATH'] = ':'.join(library_dirs + [os.environ.get('LD_LIBRARY_PATH', '')])
cuda_handles = []
for name in ['libcublasLt.so.12', 'libcublas.so.12', 'libcudnn.so.9']:
    matches = [Path(directory)/name for directory in library_dirs if (Path(directory)/name).exists()]
    if not matches:
        raise RuntimeError('Missing GPU library: ' + name)
    cuda_handles.append(ctypes.CDLL(str(matches[0]), mode=ctypes.RTLD_GLOBAL))
import av, ctranslate2
if av.__version__ != '16.0.1':
    raise RuntimeError('Restart the session after installation, then run this cell again.')
if ctranslate2.get_cuda_device_count() < 1:
    raise RuntimeError('Enable a GPU in Runtime > Change runtime type. No CPU fallback.')
print('GPU ready:', ctranslate2.get_supported_compute_types('cuda'))
""")
code("""from google.colab import files
uploaded = files.upload()
if len(uploaded) != 1:
    raise ValueError('Upload only colab_voices.zip from your backup folder.')
name, payload = next(iter(uploaded.items()))
input_package = Path('/content/colab_voices.zip')
input_package.write_bytes(payload)
del uploaded, payload
""")
code((BASE / "colab_worker.py").read_text(encoding="utf8"))
code("""# Each finished voice is checkpointed. Rerunning this cell reuses completed voices.
checkpoint = run_job(input_package)
""")
markdown("""## دانلود نتیجه
اگر پردازش را دستی متوقف کردید، همین سلول را اجرا کنید تا نتیجه‌های تمام‌شده دانلود شوند.
اگر کل سشن حذف شود، فایل‌های موقت هم از دست می‌روند؛ نتیجه‌های دانلودشده را نگه دارید.
""")
code("""# Also works after interrupting processing, if at least one file has finished.
job, job_folder = unpack_job(input_package, Path('/content/whisper_backup'))
checkpoint = job_folder/'results.json'
if not checkpoint.exists() or not json.loads(checkpoint.read_text(encoding='utf8')).get('items'):
    raise RuntimeError('No completed or failed audio results yet. Run the processing cell first.')
output_package = package_results(checkpoint)
files.download(str(output_package))
print('Return this ZIP to the local program. ChatGPT sign-in stays on your computer.')
""")
notebook = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {
        "colab": {"name": "Whisper_Colab_Backup.ipynb"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "accelerator": "GPU",
        "language_info": {"name": "python"},
    },
    "cells": cells,
}
for index, cell in enumerate(cells, 1):
    cell["id"] = f"cell-{index:03d}"
    if cell["cell_type"] == "code" and not "".join(cell["source"]).startswith("%"):
        ast.parse("".join(cell["source"]))
target = BASE / "Whisper_Colab_Backup.ipynb"
target.write_text(json.dumps(notebook, ensure_ascii=False, indent=2), encoding="utf8")
print("Notebook generated; Python cells syntax-checked:", target)
