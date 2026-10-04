"""
Interfaz de la herramienta de optimización del tizado · SUBLIMER E.I.R.L.
Ejecutar localmente:  streamlit run app.py
"""
import io
import math
import pandas as pd
import streamlit as st
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import tizado as tz

st.set_page_config(page_title='Tizado optimizado · SUBLIMER', page_icon='✂️', layout='wide')

# ----------------------------------------------------------------------------- configuración
@st.cache_data
def leer_config():
    telas = pd.read_csv('config/telas.csv')
    modelos = pd.read_csv('config/modelos.csv')
    P = pd.read_csv('config/parametros.csv').set_index('parametro')['valor'].astype(float).to_dict()
    return telas, modelos, P

TELAS, MODELOS, P0 = leer_config()
EJEMPLO = {26: 4, 28: 30, 30: 90, 32: 99, 34: 45, 36: 4}   # pedido de 272 pantalones


@st.cache_data(show_spinner=False)
def calcular_tizado(archivo_moldes, talla, W, L_max, K, params):
    """Resuelve el tizado de una talla (se guarda en caché: no se recalcula con los mismos datos)."""
    P = dict(params)
    moldes = pd.read_csv(archivo_moldes)
    tiz = tz.Tizador(moldes, P, talla, W, L_max, K)
    tiz.resolver()
    fig = tiz.dibujar()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)
    return dict(L=tiz.L, png=buf.getvalue(), verif=tiz.verificar(), segundos=tiz.segundos,
                piezas=sum(tiz.Q.values()))


@st.cache_data(show_spinner=False)
def areas_por_talla(archivo_moldes, tallas, params):
    moldes = pd.read_csv(archivo_moldes)
    return {s: tz.area_pantalon(moldes, s, dict(params)) for s in tallas}


# ----------------------------------------------------------------------------- entradas
st.title('✂️ Tizado optimizado de pantalones')
st.caption('Modelo MILP con geometría real de los moldes · SUBLIMER E.I.R.L.')

with st.sidebar:
    st.header('Datos del pedido')
    modelo = st.selectbox('Modelo de pantalón', MODELOS['modelo'])
    fila_modelo = MODELOS.set_index('modelo').loc[modelo]
    tallas = [int(t) for t in str(fila_modelo['tallas']).split(';')]

    tela_nombre = st.selectbox('Tipo de tela', TELAS['tela'])
    tela = TELAS.set_index('tela').loc[tela_nombre].to_dict()
    ancho = st.number_input('Ancho bruto medido del rollo (cm)', min_value=80.0, max_value=250.0,
                            value=float(tela['ancho_referencia_cm']), step=0.5,
                            help='Midan el rollo que van a tender; el ancho útil se calcula descontando los bordes.')
    gram, pm2 = tz.tela_derivados(tela)
    st.caption(f"{tela['peso_kg_por_metro']:.2f} kg/m · S/ {tela['precio_soles_por_metro']:.2f} por metro → "
               f"{gram:.3f} kg/m² · S/ {pm2:.2f} por m²")

    st.subheader('Pantalones por talla')
    orden = {}
    cols = st.columns(2)
    for i, s in enumerate(tallas):
        orden[s] = cols[i % 2].number_input(f'Talla {s}', min_value=0, step=1,
                                             value=int(EJEMPLO.get(s, 0)), key=f't{s}')
    st.caption(f'Total: {sum(orden.values())} pantalones')

    escenario_txt = st.radio('Forma de corte', ['Práctica actual: tizado de la talla mayor y recorte',
                                                'Un tizado por cada talla',
                                                'Comparar ambas'])
    largo_manual = st.number_input('Largo del tizado manual de la talla mayor (cm, opcional)',
                                   min_value=0.0, value=0.0, step=1.0,
                                   help='Si lo ingresan, se compara la merma del tizado manual con la del optimizado.')

    with st.expander('Opciones avanzadas'):
        auto_k = st.checkbox('Calcular automáticamente cuántos pantalones caben por capa', value=True)
        K = st.number_input('Pantalones por capa (máximo)', 1, 8, int(P0['k_pantalones']))
        capas_max = st.number_input('Capas máximas por tendido', 1, 300, int(P0['capas_max']))
        gx = st.select_slider('Separación de la rejilla (cm)', options=[2.0, 3.0, 4.0, 5.0, 6.0], value=P0['gx_cm'],
                              help='Una rejilla más fina puede dar tizados más cortos, pero tarda más.')
    calcular = st.button('Calcular tizado', type='primary', use_container_width=True)

P = dict(P0)
P['gx_cm'] = float(gx)
W = ancho - P['perdida_ancho_cm']
L_MAX = P['largo_mesa_cm'] - P['perdida_largo_cm']
params = tuple(sorted(P.items()))
archivo = fila_modelo['archivo_moldes']

if str(tela.get('nota', '')).strip() not in ('', 'nan'):
    st.warning(f'{tela_nombre}: {tela["nota"]}')

if not calcular and 'resultado' not in st.session_state:
    st.info('Ingresen los datos del pedido en el panel izquierdo y presionen **Calcular tizado**.')
    st.stop()

# ----------------------------------------------------------------------------- cálculo
if calcular:
    if sum(orden.values()) == 0:
        st.error('Ingresen al menos una cantidad por talla.')
        st.stop()
    escenarios = {'Práctica actual: tizado de la talla mayor y recorte': ['actual'],
                  'Un tizado por cada talla': ['por_talla'],
                  'Comparar ambas': ['actual', 'por_talla']}[escenario_txt]
    tallas_ped = [s for s, q in orden.items() if q > 0]
    areas = areas_por_talla(archivo, tuple(tallas), params)
    tmax = max(tallas_ped)
    necesarias = sorted({tmax} | (set(tallas_ped) if 'por_talla' in escenarios else set()), reverse=True)
    tizados, K_talla = {}, {}
    barra = st.progress(0.0, text='Calculando tizados…')
    for i, s in enumerate(necesarias):
        for k in (range(int(K), 0, -1) if auto_k else [int(K)]):
            if k * areas[s] / W > L_MAX:          # ni con 100 % de aprovechamiento cabría
                continue
            with st.spinner(f'Optimizando el tizado de la talla {s} con {k} pantalones por capa… '
                            '(la primera vez puede tardar varios minutos)'):
                try:
                    tizados[s] = calcular_tizado(archivo, s, W, L_MAX, k, params)
                    K_talla[s] = k
                    break
                except RuntimeError:
                    continue
        if s not in tizados:
            st.error(f'La talla {s} no cabe en la mesa con {int(K)} o menos pantalones por capa sobre un ancho útil de {W:.0f} cm.')
            st.stop()
        barra.progress((i + 1) / len(necesarias), text=f'Talla {s}: {K_talla[s]} pantalones por capa')
    barra.empty()
    largos = {s: tizados[s]['L'] for s in tizados}
    resultado = {}
    for esc in escenarios:
        grupos = tz.plan_de_corte(orden, esc, K_talla, int(capas_max))
        G, M, R = tz.indicadores_pedido(orden, grupos, largos, areas, tela, ancho, P)
        usados = {g['talla_tizado'] for g in grupos}
        resultado[esc] = dict(grupos=G, merma=M, resumen=R, tizados={s: tizados[s] for s in sorted(usados, reverse=True)})
        if esc == 'actual' and largo_manual > 0:
            Gm, Mm, Rm = tz.indicadores_pedido(orden, grupos, {tmax: largo_manual}, areas, tela, ancho, P)
            resultado['manual'] = dict(grupos=Gm, merma=Mm, resumen=Rm)
    st.session_state['resultado'] = resultado
    st.session_state['contexto'] = dict(tela=tela_nombre, W=W, orden=orden, modelo=modelo, K=K_talla)

resultado = st.session_state['resultado']
ctx = st.session_state['contexto']
NOMBRES = {'actual': 'Práctica actual (talla mayor + recorte)', 'por_talla': 'Un tizado por talla',
           'manual': 'Tizado manual (práctica actual)'}

# ----------------------------------------------------------------------------- salidas
tab1, tab2, tab3 = st.tabs(['📐 Plan de corte y plantillas', '📊 Indicadores de merma', 'ℹ️ Datos usados'])

with tab1:
    for esc in [e for e in ('actual', 'por_talla') if e in resultado]:
        r = resultado[esc]
        st.subheader(NOMBRES[esc])
        tabla = r['grupos'][['grupo', 'talla_tizado', 'pantalones_por_capa', 'capas', 'largo_tizado_m', 'metros_de_tela']].rename(columns={
            'grupo': 'Grupo de capas', 'talla_tizado': 'Talla del tizado', 'pantalones_por_capa': 'Pantalones por capa', 'capas': 'Capas',
            'largo_tizado_m': 'Largo del tizado (m)', 'metros_de_tela': 'Metros de tela'})
        st.dataframe(tabla, hide_index=True, use_container_width=True)
        c1, c2 = st.columns(2)
        c1.metric('Pantalones cortados', r['resumen']['pantalones_cortados'],
                  delta=r['resumen']['pantalones_cortados'] - r['resumen']['pantalones_pedidos'],
                  delta_color='off', help='La diferencia con lo pedido son pantalones sobrantes por redondeo de capas.')
        c2.metric('Metros de tela', f"{r['resumen']['metros_lineales']:.1f} m")
        if esc == 'actual':
            st.caption('Todo el pedido se marca con el tizado de la talla mayor; las piezas de las tallas menores se recortan después.')
        st.download_button('Descargar plan de corte (CSV)', r['grupos'].to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'plan_de_corte_{esc}.csv', mime='text/csv', key=f'plan_{esc}')
        for s, tzd in r['tizados'].items():
            grupos_s = r['grupos'][r['grupos']['talla_tizado'] == s]
            with st.expander(f'Plantilla de la talla {s} · {int(grupos_s["capas"].sum())} capas · '
                             f'largo {tzd["L"] / 100:.2f} m', expanded=True):
                st.image(tzd['png'], use_container_width=True)
                v = tzd['verif']
                st.caption(('✅' if all(v.values()) else '❌') + ' Sin superposiciones, todas las piezas colocadas y dentro de la tela'
                           f' · {tzd["piezas"]} piezas · cálculo en {tzd["segundos"]:.0f} s')
                st.download_button('Descargar plantilla (PNG)', tzd['png'], file_name=f'plantilla_talla_{s}.png',
                                   mime='image/png', key=f'dl_{esc}_{s}')
        st.divider()

with tab2:
    def mostrar_merma(r, titulo):
        st.subheader(titulo)
        tot = r['merma'].set_index('componente').loc['Total']
        a, b, c, d = st.columns(4)
        a.metric('Merma total', f"{tot['porcentaje']:.2f} %")
        b.metric('Merma en m²', f"{tot['m2']:.2f} m²")
        c.metric('Merma en kg', f"{tot['kg']:.1f} kg")
        d.metric('Merma en soles', f"S/ {tot['soles']:,.2f}")
        t = r['merma'].rename(columns={'componente': 'Componente', 'm2': 'm²', 'kg': 'kg', 'soles': 'S/', 'porcentaje': '%'})
        st.dataframe(t.style.format({'m²': '{:.2f}', 'kg': '{:.2f}', 'S/': '{:,.2f}', '%': '{:.2f}'}),
                     hide_index=True, use_container_width=True)
        st.caption(f"Tela usada: {r['resumen']['metros_lineales']:.1f} m lineales = {r['resumen']['tela_m2']:.1f} m² = "
                   f"{r['resumen']['tela_kg']:.1f} kg = S/ {r['resumen']['tela_soles']:,.2f}")
        st.download_button('Descargar indicadores (CSV)', r['merma'].to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'indicadores_{titulo[:20]}.csv', mime='text/csv', key=f'ind_{titulo}')

    for esc in [e for e in ('manual', 'actual', 'por_talla') if e in resultado]:
        mostrar_merma(resultado[esc], NOMBRES[esc] + (' (optimizado)' if esc == 'actual' else ''))
        st.divider()

    claves = [e for e in ('manual', 'actual', 'por_talla') if e in resultado]
    if len(claves) > 1:
        st.subheader('Comparación')
        comp = pd.DataFrame([dict(Escenario=NOMBRES[e], **{
            'Merma %': resultado[e]['merma'].set_index('componente').loc['Total', 'porcentaje'],
            'Merma kg': resultado[e]['merma'].set_index('componente').loc['Total', 'kg'],
            'Merma S/': resultado[e]['merma'].set_index('componente').loc['Total', 'soles'],
            'Tela kg': resultado[e]['resumen']['tela_kg']}) for e in claves])
        st.dataframe(comp.style.format({'Merma %': '{:.2f}', 'Merma kg': '{:.1f}', 'Merma S/': '{:,.2f}', 'Tela kg': '{:.1f}'}),
                     hide_index=True, use_container_width=True)
        base = comp.iloc[0]
        for _, f in comp.iloc[1:].iterrows():
            st.success(f"{f['Escenario']}: ahorra {base['Tela kg'] - f['Tela kg']:.1f} kg de tela "
                       f"(S/ {base['Merma S/'] - f['Merma S/']:,.2f}) frente a {base['Escenario']}.")
        if 'por_talla' in resultado:
            st.caption('El tizado por talla elimina el recorte, pero requiere más tendidos: consideren el tiempo adicional de extendido.')

with tab3:
    st.write(f"**Modelo:** {ctx['modelo']} · **Tela:** {ctx['tela']} · **Ancho útil:** {ctx['W']:.1f} cm")
    st.write('**Pantalones por capa que caben en cada tizado:** ' + ', '.join(f'talla {s}: {k}' for s, k in ctx['K'].items()))
    st.dataframe(pd.DataFrame({'Talla': list(ctx['orden']), 'Pantalones': list(ctx['orden'].values())}), hide_index=True)
    st.dataframe(TELAS, hide_index=True)
    st.caption('Los datos de telas, modelos y parámetros se editan en los archivos de la carpeta config.')
