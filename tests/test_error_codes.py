# -*- coding: utf-8 -*-
"""Etapa 10.1: `error_code` estavel e independente de idioma nas respostas de erro.

A mensagem tecnica original (pt-BR) continua no campo `error` (diagnostico); o campo
`error_code` permite a interface traduzir (frontend/js/i18n.js, mapa literal) e
`error_details` carrega valores estruturados para interpolacao. Contrato aditivo:
respostas antigas continuam com `error`; o codigo e extra quando conhecido."""

from tests.conftest import wait_terminal


def test_geracao_sem_prompt_tem_codigo_estavel(client):
    response = client.post("/api/generations", json={"module": "image", "prompt": " ", "ratio": "1:1", "tier": "720p"})
    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "prompt_required"
    assert "prompt" in body["error"].lower()


def test_geracao_com_steps_fora_do_perfil_tem_codigo_e_detalhes(client):
    response = client.post(
        "/api/generations",
        json={"module": "image", "prompt": "teste", "ratio": "1:1", "tier": "720p", "profile": "z-image-turbo", "steps": 20},
    )
    # O contrato do perfil e validado antes da disponibilidade de pesos.
    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "invalid_steps"
    assert body["error_details"] == {"min": 1, "max": 12, "received": 20}


def test_perfil_desconhecido_tem_codigo(client):
    response = client.post(
        "/api/generations",
        json={"module": "image", "prompt": "teste", "ratio": "1:1", "tier": "720p", "profile": "mistral"},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "unknown_profile"


def test_candidato_no_motor_simulado_e_recusado_com_codigo(client):
    """Motor simulado so atende sdxl: a recusa do candidato vem ANTES da checagem de pesos."""
    response = client.post(
        "/api/generations",
        json={"module": "image", "prompt": "teste", "ratio": "1:1", "tier": "720p", "profile": "z-image-turbo"},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "simulated_profile_rejected"


def test_cancelamento_tem_codigo(client):
    # Id desconhecido e job terminal: o cancelamento nao confirma (409, codigo estavel).
    response = client.post("/api/generations/ffffffffffffffffffffffffffffffff/cancel")
    assert response.status_code == 409
    assert response.json()["error_code"] == "generation_not_running"
    criada = client.post(
        "/api/generations", json={"module": "image", "prompt": "cidade", "ratio": "1:1", "tier": "720p"}
    ).json()["generation"]
    final = wait_terminal(client, criada["id"])
    response = client.post(f"/api/generations/{final['id']}/cancel")
    assert response.status_code == 409
    assert response.json()["error_code"] == "generation_not_running"


def test_geracao_inexistente_404_tem_codigo(client):
    response = client.get("/api/generations/ffffffffffffffffffffffffffffffff")
    assert response.status_code == 404
    assert response.json()["error_code"] == "generation_not_found"


def test_i2i_sem_referencia_tem_codigo(client):
    response = client.post(
        "/api/generations",
        json={"module": "image", "prompt": "x", "ratio": "1:1", "tier": "720p", "mode": "i2i"},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "i2i_requires_reference"


def test_upload_de_referencia_invalida_tem_codigo(client):
    response = client.post("/api/references", content=b"nao-e-imagem", headers={"x-filename": "a.png"})
    assert response.status_code == 415
    assert response.json()["error_code"] == "reference_invalid_image"


def test_projeto_inexistente_tem_codigo(client):
    response = client.get("/api/projects/ffffffffffffffffffffffffffffffff")
    assert response.status_code == 404
    assert response.json()["error_code"] == "project_not_found"


def test_liberar_memoria_sem_confirmacao_tem_codigo(client):
    response = client.post("/api/system/engine/free", json={"confirm": False})
    assert response.status_code == 409
    assert response.json()["error_code"] in ("free_missing_confirm", "free_not_dedicated", "free_not_supported")


def test_erro_sem_codigo_conhecido_nao_inclui_error_code(client):
    """ValueError generico (sem codigo) mantem o contrato antigo: so `error`."""
    response = client.post("/api/generations", json={"module": "image", "prompt": "x", "ratio": "invalida", "tier": "720p"})
    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "unknown_ratio"  # presets.py agora e codificado
    assert "Proporcao invalida" in body["error"] or "proporcao" in body["error"].lower()
