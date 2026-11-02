# Ranking de clausulazos – Los liguis 2.0

Lee el feed de la liga en Mister, registra cada fichaje por pago de cláusula y los publica en una web estática gratuita (GitHub Pages).

```
scraper/mister_clausulazos.py   lee el feed y actualiza docs/data/clausulazos.json
docs/                           la web (index.html, app.js, style.css, data/)
.github/workflows/actualizar.yml  lo ejecuta cada 3 horas y guarda los cambios
worker/index.js                 servicio (Cloudflare Worker) para añadir clausulazos a mano con contraseña
docs/config.js                  aquí se pone la URL del Worker
tests/sample_feed.html          ejemplo de feed para probar el parser
```

## Puesta en marcha

1. **Crea un repositorio** en GitHub (público: GitHub Pages gratis lo exige) y sube esta carpeta.
2. **Guarda tu sesión de Mister como secreto.** Entra en Mister con Google, abre el feed de Los liguis 2.0 y pulsa F12 → pestaña *Red*. Recarga, pulsa la petición `feed` y, en *Cabeceras de la solicitud*, copia el valor completo de `Cookie`. En GitHub: *Settings → Secrets and variables → Actions → New repository secret*, con nombre `MISTER_COOKIE`.
   - La cookie es como una contraseña: no la pegues en archivos del repositorio ni la compartas.
   - Si tienes varias ligas, copia la cookie con Los liguis 2.0 seleccionada.
   - Caduca cada cierto tiempo. Cuando eso pase, el workflow fallará con un aviso y bastará con pegar una cookie nueva en el secreto.
3. **Activa la web:** *Settings → Pages → Build and deployment → Deploy from a branch → `main` / carpeta `/docs`*.
4. **Primera ejecución:** pestaña *Actions → Actualizar clausulazos → Run workflow*. Tu web quedará en `https://<usuario>.github.io/<repositorio>/`.

## Añadir clausulazos a mano (con contraseña)

Sirve para meter los clausulazos antiguos que ya no aparecen en el feed. GitHub Pages es estático y una contraseña escrita en el código de la página la podría leer cualquiera, así que la contraseña se comprueba en un pequeño servicio gratuito (un Cloudflare Worker) que es el único que puede escribir en el repositorio. Los datos manuales se guardan en `docs/data/manual.json`, separados de los del feed.

1. **Token de GitHub:** *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate*. Limítalo a este repositorio, con el permiso *Contents: Read and write*. Cópialo.
2. **Worker:** en [Cloudflare](https://dash.cloudflare.com) (cuenta gratuita) → *Workers & Pages → Create → Worker → Deploy*. Pulsa *Edit code*, pega el contenido de `worker/index.js` y despliega.
3. **Variables del Worker** (*Settings → Variables and Secrets*):
   - Texto: `ALLOWED_ORIGIN` = `https://TU-USUARIO.github.io` (sin barra final ni ruta) y `GITHUB_REPO` = `TU-USUARIO/TU-REPOSITORIO`. Opcional: `GITHUB_BRANCH` (por defecto `main`).
   - Secretos: `ADMIN_PASSWORD` (elige una larga) y `GITHUB_TOKEN` (el del paso 1).
4. **Conecta la web:** copia la URL del Worker (`https://…workers.dev`) en `docs/config.js`, en `addEndpoint`, y sube el cambio.

Al final de la web aparecerá «Añadir clausulazos a mano». Se desbloquea con la contraseña (que se comprueba siempre en el Worker y no se guarda en el navegador), y permite añadir uno a uno o pegar varios con el formato `fecha;jugador;vendedor;comprador;importe`. Quien añade los ve al instante; el resto, en torno a un minuto, cuando GitHub Pages se actualiza.

- Si te equivocas en un dato, edita o borra la entrada en `docs/data/manual.json` desde GitHub.
- Los nombres se agrupan sin distinguir mayúsculas ni tildes («Muñoz» y «munoz» cuentan como el mismo manager), pero conviene escribirlos igual que en Mister; el formulario sugiere los ya existentes.
- Un clausulazo manual que coincida con uno del feed (mismo jugador, vendedor, comprador e importe) no se cuenta dos veces.
- Para cambiar la contraseña, cambia el secreto `ADMIN_PASSWORD` en Cloudflare.

## Usar la web dentro de Google Sites (opcional)

En Google Sites: *Insertar → Incrustar → Por URL* y pega la dirección de GitHub Pages.

## Probar en local

```bash
pip install -r requirements.txt
python scraper/mister_clausulazos.py --html tests/sample_feed.html --out /tmp/prueba.json
MISTER_COOKIE="..." python scraper/mister_clausulazos.py   # contra Mister de verdad
```

## Limitaciones conocidas

- El feed solo muestra los movimientos más recientes. Si entre dos ejecuciones hay más movimientos de los que caben en el feed, se pierden los más antiguos. Si pasa, acorta el `cron` del workflow.
- El feed no muestra hora en las entradas: la fecha que se guarda es la de detección.
- Un mismo jugador clausulado entre los mismos dos managers por el mismo importe se cuenta una sola vez.
- Esto lee tu propia cuenta de Mister sin API oficial. Si Mister cambia el HTML del feed, habrá que ajustar el parser.
- La web es pública y muestra los nombres de manager de la liga.
