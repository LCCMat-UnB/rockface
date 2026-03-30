# Projeto RockFace
### Guia de Contribuição e Diretrizes de Programação

Bem-vindo ao repositório do **RockFace**. Este projeto utiliza Inteligência Artificial (agentes multimodais) para o reconhecimento e classificação automatizada de lâminas de rocha do campo de **Libra**. 

Para manter a consistência entre pesquisadores e desenvolvedores, todos os colaboradores devem seguir as diretrizes abaixo.

---

### 1. Idioma e Documentação
Seguimos uma regra estrita de bilinguismo funcional:

* **Dentro do Código (Technical English):** Todos os comentários técnicos, nomes de variáveis, funções e *docstrings* devem ser escritos em **Inglês**.
* **Fora do Código (Documentação em Português):** Explicações em arquivos Markdown (`.md`), READMEs, células de texto em Jupyter Notebooks e comunicações de progresso devem ser em **Português (Brasil)**.

---

### 2. Padrões de Codificação (Python)

#### 2.1 Nomenclatura
* **Variáveis e Funções:** `snake_case` (ex: `calculate_porosity()`).
* **Classes:** `PascalCase` (ex: `SegmentationAgent`).
* **Constantes:** `UPPER_CASE` (ex: `BATCH_SIZE = 32`).
* **Prefixos de Agentes:** Para identificar a qual parte da arquitetura o código pertence:
    * `seg_`: Arquivos ou classes do **Agente de Segmentação**.
    * `cls_`: Arquivos ou classes dos **Agentes Especialistas** (Grãos, Poros, Cimento).

#### 2.2 Docstrings
Utilize o padrão **Google Style**. Toda função complexa deve conter descrição, parâmetros e tipo de retorno.
```python
def seg_process_image(image_path: str) -> np.ndarray:
    """
    Loads a thin section image and applies the initial segmentation mask.
    
    Args:
        image_path: Path to the high-resolution rock image.
        
    Returns:
        A numpy array representing the segmented regions.
    """
```

---

### 3. Arquitetura e IA

#### 3.1 Estrutura de Agentes
O projeto é dividido em duas grandes frentes:
1.  **Agente de Segmentação:** Mapeia macrorregiões (grãos, poros e cimento).
2.  **Agentes Especialistas:** Três modelos independentes que classificam os dados refinados pela segmentação.

#### 3.2 Reprodutibilidade
É obrigatório fixar as *seeds* de todas as bibliotecas aleatórias no início de scripts de treinamento:
```python
import torch
import numpy as np
import random

seed = 42
torch.manual_seed(seed)
np.random.seed(seed)
random.seed(seed)
```

---

### 4. Controle de Versão (Git)
* **Commits:** Tente usar mensagens claras (ex: `feat(seg): add ResNet backbone` ou `fix(data): remove corrupted images`).
* **Dados:** Nunca suba arquivos de imagem `.jpg` ou `.png` pesados diretamente para o GitHub. Utilize o `.gitignore` e ferramentas como DVC ou links de nuvem para o *dataset* curado.

---
*Este documento é mantido pela equipe do projeto RockFace (LCCMat/UnB/Petrobras). Em caso de dúvidas, consulte o coordenador do módulo.*

---
