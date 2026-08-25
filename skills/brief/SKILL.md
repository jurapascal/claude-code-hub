---
name: brief
description: Přečte briefing projektu a vytáhne z něj strukturovaná fakta do poznámky v paměti
---

Vytáhni z volně psaného briefingu projektu strukturovaná fakta a ulož je.

## Odkud briefing vzít

V tomhle pořadí, první nalezený vyhrává:

1. Blok mezi `<!-- hub:briefing -->` a `<!-- /hub:briefing -->` v `CLAUDE.md`
   v kořeni projektu — tam ho ukládá Hub.
2. Klíč `brief` u cesty tohoto projektu v `{{CLAUDE_DIR}}/hub-projects.json`.
3. `$ARGUMENTS` — když uživatel briefing napsal rovnou do příkazu.

Když není ani jedno, řekni to a nabídni, ať ho napíše: pravý klik na projekt
v panelu → **Upravit…** → pole *Briefing*. Nic si nevymýšlej.

## Co z něj vytáhnout

Jen to, co v textu **opravdu je**. Co tam není, se nedoplňuje ani nehádá —
prázdná kolonka je lepší než smyšlená.

- **Co to je** — jedna věta, čemu projekt slouží a komu
- **Stack** — jazyk, framework, databáze, build
- **Hosting a doména** — kde to běží
- **Deploy** — jak se to nasazuje (FTP / git / ruční), co je zdroj pravdy
- **Klient a kontakt** — kdo si to objednal, s kým se to řeší
- **Na co pozor** — pasti, křehká místa, co už jednou nevyšlo
- **Stav** — co je hotové a co se zrovna dělá

## Postup

1. Načti briefing podle pořadí výše.
2. Projdi projekt a **ověř, co jde ověřit** — existuje `.ftp-deploy.json`?
   `package.json`, `composer.json`, git remote? Když si briefing s repem
   odporuje, napiš to; skutečnost z disku má přednost před tím, co si někdo
   pamatoval.
3. Zapiš poznámku do paměti jako `{{MEMORY_DIR}}/project_<slug>.md` ve formátu,
   který tam mají ostatní poznámky (frontmatter `name`/`description`/
   `metadata.type: project`, tělo, `[[wikilinky]]` na související).
   Když už poznámka existuje, **aktualizuj ji**, nezakládej druhou.
4. Přidej jeden řádek do `{{MEMORY_DIR}}/MEMORY.md` do sekce *Projekty
   a reference* — pod 120 znaků, ať se rozcestník vejde do kontextu.
5. Nakonec vypiš, co jsi vytáhl, a zvlášť to, co v briefingu chybělo a stálo
   by za doplnění.

## Čeho se držet

- Fakta z briefingu neopisuj slovo od slova do poznámky — destiluj je.
- Do `CLAUDE.md` projektu nesahej: ten blok patří Hubu a přepsal bys ho.
- Jména klientů a kontakty patří do paměti, ne do commitu.
