# Correções pt-BR no frontend Sure

Patch gerado contra `erlancassiano/sure` @ `f569fb7` (main).

## Aplicar no clone do Sure

```bash
cd /caminho/para/sure
git checkout -b cursor/fix-pt-br-translations-8d40
git apply sure-pt-br-i18n.patch
# ou copiar os arquivos de sure-pt-br-locales/
git add config/locales
git commit -m "fix(i18n): corrigir traduções pt-BR incorretas no frontend"
```

## Correções

| Antes | Depois | Motivo |
|-------|--------|--------|
| Descarregar | Baixar | pt-PT → pt-BR |
| em falta | ausentes | pt-PT → pt-BR |
| TAE | Juros a.a. | espanholismo (APR) |
| Venda de Negócios | Negociações de venda | tradução absurda |
| Holdings / Holding | Posições / Posição | inglês residual |
| Principais Aplicações | Principais posições | inconsistente |
| Trocas | Negociações | sentido errado (trades) |
| Taxa limitada | Limite de requisições atingido | "rate limited" mal traduzido |
| Resetar / resetada | Redefinir / redefinida | anglicismo |
| Saldo de abertura/fechamento | Saldo inicial/final | extrato BR |
| Demo aberta | Abrir demo | CTA, não adjetivo |
| voce@exemplo.com | você@exemplo.com | acentuação |
| As suas edições | Suas edições | pt-PT |
| base de código | projeto | calque de codebase |
| App de Produção, Dashboard de Analytics | App de orçamento, Rastreador de portfólio | inglês residual |
| Importado/Vistos (transações) | Importadas/Vistas | concordância |
