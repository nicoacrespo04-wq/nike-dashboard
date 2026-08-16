/**
 * Lectura del glosario de factores que publica el backend.
 *
 * Fuente única: `backend/app/api/glossary.py` (y su gemelo generado
 * `backend/docs/glossary.md`). Viaja adjunto —campo `glossary`— en las
 * respuestas que publican contribuciones al score:
 *
 *   `/api/matches/{id}`            → competitive_match
 *   `/api/products/{id}/matches`   → competitive_match
 *   `/api/retail-media`            → retail_media + business_importance
 *
 * Por qué existe este módulo: la UI no puede mostrar "editorial: 12% de
 * contribución" y llamar a eso explicabilidad. Cada factor tiene que poder
 * contestar QUÉ MIDE, CON QUÉ DATOS y CÓMO SE LEE un valor alto o bajo, que es
 * exactamente lo que trae cada término. Acá se resuelve el término de un factor
 * sin importar en qué familia esté declarado.
 *
 * El glosario es opcional en todos los contratos: un backend viejo no lo manda
 * y la pantalla tiene que seguir funcionando (sin tooltip, no rota).
 */

import type {
  Glossary,
  GlossaryGroup,
  GlossaryGroupName,
  GlossaryTerm,
} from "@/types/intelligence";

/** Busca una familia de términos. `null` si el backend no la publicó. */
export function glossaryGroup(
  glossary: Glossary | null | undefined,
  group: GlossaryGroupName,
): GlossaryGroup | null {
  const found = glossary?.[group];
  return found && Array.isArray(found.terms) ? found : null;
}

/**
 * Índice `nombre del factor` → término.
 *
 * Es un objeto plano y no una función a propósito: estas pantallas son Server
 * Components que le pasan el glosario a componentes cliente (la tabla de
 * factores es interactiva), y React no puede serializar una función a través de
 * esa frontera — pasar un buscador reventaba el render del match con
 * "Functions cannot be passed directly to Client Components".
 */
export type GlossaryTerms = Record<string, GlossaryTerm>;

/**
 * Arma el índice de términos sobre una o varias familias.
 *
 * Se pasan varias cuando una misma pantalla mezcla familias: retail media
 * publica sus 7 factores y, en la misma respuesta, los 11 de business
 * importance, porque uno de sus drivers ES el score de business importance.
 * Ante nombres repetidos gana la primera familia listada.
 */
export function termIndex(
  glossary: Glossary | null | undefined,
  ...groups: GlossaryGroupName[]
): GlossaryTerms {
  const index: GlossaryTerms = {};
  const wanted: GlossaryGroupName[] =
    groups.length > 0 ? groups : (Object.keys(glossary ?? {}) as GlossaryGroupName[]);

  for (const group of wanted) {
    for (const term of glossaryGroup(glossary, group)?.terms ?? []) {
      if (typeof term.name === "string" && !(term.name in index)) index[term.name] = term;
    }
  }
  return index;
}

/** Término de un factor, o `null` si el backend no lo publicó. */
export function termFor(
  terms: GlossaryTerms | null | undefined,
  name: string | null | undefined,
): GlossaryTerm | null {
  if (!terms || typeof name !== "string") return null;
  return terms[name] ?? null;
}

/** `true` si el término tiene algo que decir (el backend puede publicarlo vacío). */
export function isDefined(term: GlossaryTerm | null): term is GlossaryTerm {
  return term !== null && term.definition.trim() !== "";
}
