# Third-party notices

The MIT license in [LICENSE](LICENSE) covers the ImageCore AI project code. The project records the reused infrastructure as the author's own code from an earlier personal project.

## Runtime and development tools

The Python packages are installed separately from their package distributions.

| Package | License |
| --- | --- |
| [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE) | MIT |
| [Uvicorn](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) | BSD 3-Clause |
| [HTTPX](https://github.com/encode/httpx/blob/master/LICENSE.md) | BSD 3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE) | MIT |
| [PyYAML](https://github.com/yaml/pyyaml/blob/main/LICENSE) | MIT |
| [Pillow](https://github.com/python-pillow/Pillow/blob/main/LICENSE) | MIT-CMU |
| [pytest](https://github.com/pytest-dev/pytest/blob/main/LICENSE) | MIT |
| [setuptools](https://github.com/pypa/setuptools/blob/main/LICENSE) | MIT |

The interface uses system-installed fonts. The repository includes no font files.

## ComfyUI and model weights

ImageCore AI communicates with [ComfyUI](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE) over HTTP. ComfyUI is a separately installed GPL-3.0 application. Install model weights separately under the terms of their respective licenses.

| Model | Upstream license |
| --- | --- |
| [SDXL Base 1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md) | CreativeML Open RAIL++-M |
| [FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/blob/main/LICENSE.md) | Apache 2.0 |
| [Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo) | Apache 2.0 |

## Adapted workflow templates

- `config/workflows/t2i.sdxl.json`, `i2i.sdxl.json` and `t2i.example.json` contain declarative API graphs assembled from the documented [text-to-image](https://github.com/Comfy-Org/workflow_templates/blob/main/site/knowledge/concepts/text-to-image.md) and [image-to-image](https://github.com/Comfy-Org/workflow_templates/blob/main/site/knowledge/concepts/img2img.md) nodes. Their node IDs match the application's configuration.
- `config/workflows/t2i.flux2-klein.json` adapts the [FLUX.2 Klein text-to-image template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_flux2_klein_text_to_image.json).
- `config/workflows/t2i.z-image-turbo.json` adapts the [Z-Image-Turbo int8 template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_z_image_turbo_int8.json).

Those templates retain the [upstream MIT license](https://github.com/Comfy-Org/workflow_templates/blob/main/LICENSE).

```text
MIT License

Copyright (c) 2023-present Comfy Org

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
