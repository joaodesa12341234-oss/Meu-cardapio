import os

import ambiente


def test_carrega_env_sem_sobrescrever(tmp_path, monkeypatch):
    arq = tmp_path / ".env"
    arq.write_text('# comentário\nTESTE_A=1\nTESTE_B="com aspas"\nTESTE_C=\nTESTE_D=do_arquivo\nlixo sem igual\n'
                   "TESTE_E='simples'\n", encoding="utf-8")
    for n in "ABCDE":
        monkeypatch.delenv(f"TESTE_{n}", raising=False)
    monkeypatch.setenv("TESTE_D", "do_terminal")
    monkeypatch.setenv("SEM_ENV", "0")
    assert ambiente.carregar(arq) == 3
    assert os.environ["TESTE_A"] == "1" and os.environ["TESTE_B"] == "com aspas" and os.environ["TESTE_E"] == "simples"
    assert "TESTE_C" not in os.environ and os.environ["TESTE_D"] == "do_terminal"
    for n in "ABE":
        monkeypatch.delenv(f"TESTE_{n}", raising=False)


def test_arquivo_inexistente_nao_quebra(tmp_path, monkeypatch):
    monkeypatch.setenv("SEM_ENV", "0")
    assert ambiente.carregar(tmp_path / "nao-existe.env") == 0
