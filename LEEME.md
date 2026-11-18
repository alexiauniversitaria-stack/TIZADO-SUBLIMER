# Interfaz de tizado optimizado · SUBLIMER E.I.R.L.

## Contenido de la carpeta
| Archivo | Para qué sirve |
|---|---|
| `app.py` | La interfaz (pantallas, botones, pestañas). |
| `tizado.py` | El modelo MILP con geometría real: conjuntos, restricciones, solución por bloques e indicadores. `app.py` lo importa. |
| `requirements.txt` | Librerías que Streamlit instala al publicar. |
| `config/telas.csv` | Tela: ancho de referencia, peso por metro lineal y precio por metro lineal. |
| `config/modelos.csv` | Modelos de pantalón: género, archivo de moldes, tallas, talla base de los moldes y precio de venta. |
| `config/parametros.csv` | Mesa, pérdidas de la tela, pantalones por capa, capas por tendido, graduación y parámetros de cálculo. |
| `moldes/pantalon_cargo.csv` | Pantalón cargo (hombre), moldes en talla 32. |
| `moldes/pantalon_clasico_beige.csv` | Pantalón clásico beige (hombre), moldes en talla 32. |
| `moldes/pantalon_jogger.csv` | Pantalón jogger (hombre), moldes en talla 34. |
| `moldes/pantalon_jean_clasico_mujer.csv` | Pantalón jean clásico mujer, moldes en talla 28. |

Columnas de los moldes: `molde, n_por_pantalon, grupo (G/P), permite_90 (1/0), par_espejo (1/0), gradua (1/0), vertice, x_cm, y_cm, talla`.
La columna `talla` se llena solo en piezas con medida propia por talla (la pretina); si está vacía, la pieza es la misma para todas las tallas o se gradúa según `gradua`.

## Publicar en Streamlit Community Cloud (paso a paso)
1. **Crear la cuenta de GitHub.** Entrar a github.com → *Sign up* y seguir los pasos con su correo.
2. **Crear el repositorio.** Botón **+** (arriba a la derecha) → *New repository* → nombre `tizado-sublimer` → marcar **Private** → *Create repository*.
3. **Subir los archivos.** En el repositorio vacío, clic en *uploading an existing file* → arrastrar **todo el contenido de esta carpeta** (incluidas las carpetas `config` y `moldes`) → abajo, *Commit changes*. Verificar que en GitHub se vean `app.py`, `tizado.py`, `requirements.txt` y las dos carpetas.
4. **Crear la cuenta de Streamlit.** Entrar a share.streamlit.io → *Continue with GitHub* → autorizar el acceso, **incluido el acceso a repositorios privados**.
5. **Crear la aplicación.** *Create app* → *Deploy a public app from GitHub* (o la opción equivalente) → Repository: `tizado-sublimer`; Branch: `main`; Main file path: `app.py`; App URL: un nombre corto, por ejemplo `tizado-sublimer` → *Deploy*.
6. **Esperar la instalación.** La primera vez tarda unos minutos mientras instala las librerías de `requirements.txt`. Al terminar, se abre la interfaz.
7. **Probar.** Elegir un modelo, ingresar las cantidades por talla y presionar **Calcular tizado**. La primera vez cada talla tarda en calcularse; después queda guardada.
8. **Compartir.** En la app, *Share* (arriba a la derecha) → invitar por correo al jefe de producción. Mantener la app privada.

## Cómo se vincula la interfaz con el modelo
- `app.py` hace `import tizado as tz` y usa: `tz.Tizador(...)` para resolver cada tizado, `tz.plan_de_corte(...)` para calcular capas y tendidos, y `tz.indicadores_pedido(...)` para la merma en m², kg, soles y %.
- Los datos no están dentro del código: se leen de `config/` y `moldes/`. Para cambiar un precio, una medida o un molde, se edita el CSV en GitHub (ícono del lápiz → *Commit changes*) y la app se actualiza sola en uno o dos minutos.

## Problemas frecuentes
- **La app se “duerme”** si nadie la usa por varios días: presionar el botón para despertarla y esperar.
- **Error al iniciar:** en la app, *Manage app* (abajo a la derecha) → revisar el registro y enviar una captura.
- **Una talla no cabe:** la app prueba automáticamente con menos pantalones por capa; si ni con uno cabe, revisar el ancho del rollo y el largo de la mesa en `config/`.

## Probar en una computadora (opcional)
```
pip install -r requirements.txt
streamlit run app.py
```
