"""
Motor de cálculo del tizado (modelo MILP con geometría real sobre rejilla, Capítulo III).
Reúne en funciones el código de los puntos de control 1 a 4 del cuaderno de Colab,
para que la interfaz de Streamlit pueda usarlo.
"""
import math
import time
import numpy as np
import pandas as pd
from matplotlib.path import Path
from scipy.signal import correlate
import pulp


# ----------------------------------------------------------------------------- geometría
def area_poligono(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def desplazar_contorno(pts, d):
    """Amplía (d > 0) o reduce (d < 0) el contorno una distancia d, con esquinas en punta."""
    if d == 0:
        return pts
    from shapely.geometry import Polygon
    g = Polygon(pts).buffer(d, join_style='mitre')
    if g.is_empty or g.area < 1.0:
        return None                                   # la pieza desaparece con ese desplazamiento
    if g.geom_type == 'MultiPolygon':
        g = max(g.geoms, key=lambda p: p.area)
    return np.array(g.exterior.coords)[:-1]


def rotar(pts, ang):
    a = math.radians(ang)
    c, s = math.cos(a), math.sin(a)
    q = pts @ np.array([[c, s], [-s, c]])
    return q - q.min(axis=0)


def mascara(pts, R):
    w, h = pts[:, 0].max(), pts[:, 1].max()
    nx, ny = int(math.ceil(w / R - 1e-9)), int(math.ceil(h / R - 1e-9))
    xs, ys = (np.arange(nx) + 0.5) * R, (np.arange(ny) + 0.5) * R
    XX, YY = np.meshgrid(xs, ys, indexing='ij')
    return Path(pts).contains_points(np.c_[XX.ravel(), YY.ravel()]).reshape(nx, ny)


def graduar(pts, talla, P, gradua=True):
    """Desplaza el contorno 1 cm por lado por cada talla. Las piezas con gradua = 0 no cambian."""
    if not gradua:
        return pts
    j = (talla - P['talla_base']) / P['paso_talla']
    g = desplazar_contorno(pts, j * P['graduacion_cm'])
    if g is None:
        raise ValueError(f'Una pieza desaparece al graduarla a la talla {talla}: marque gradua = 0 para esa pieza en el CSV de moldes.')
    return g


def _gradua(g):
    if 'talla' in g and g['talla'].notna().any():
        return False                                  # pieza con medida propia por talla: no se gradúa
    return 'gradua' not in g or int(g['gradua'].iloc[0]) == 1


def piezas_de_talla(moldes_df, talla):
    """
    Agrupa los vértices por molde para una talla.
    Si una pieza tiene la columna 'talla' llena (por ejemplo, la pretina), se usan solo
    los vértices de esa talla; si está vacía, la pieza es la misma para todas las tallas
    (y se gradúa según la columna 'gradua').
    """
    df = moldes_df
    if 'talla' in df:
        t = pd.to_numeric(df['talla'], errors='coerce')
        df = df[t.isna() | (t == int(talla))]
        faltan = {m for m in set(moldes_df.loc[t.notna(), 'molde']) if m not in set(df['molde'])}
        if faltan:
            raise ValueError(f'Falta la medida de {", ".join(sorted(faltan))} para la talla {talla} en el CSV de moldes.')
    return df.groupby('molde', sort=False)


def area_pantalon(moldes_df, talla, P):
    """Área (cm²) de las piezas de un pantalón en una talla."""
    total = 0.0
    for m, g in piezas_de_talla(moldes_df, talla):
        g = g.sort_values('vertice')
        pts = graduar(g[['x_cm', 'y_cm']].to_numpy(float), talla, P, _gradua(g))
        total += int(g['n_por_pantalon'].iloc[0]) * area_poligono(pts)
    return total


# ----------------------------------------------------------------------------- tizador
class Tizador:
    """Construye los conjuntos (punto de control 1), formula (2) y resuelve (3) el tizado de una talla."""

    def __init__(self, moldes_df, P, talla, W, L_max, K):
        self.df, self.P, self.talla, self.W, self.L_MAX, self.K = moldes_df, P, talla, W, L_max, K
        self.R = P['resolucion_cm']
        self.GX, self.GY = P['gx_cm'], P['gy_cm']
        self.SX, self.SY = int(round(self.GX / self.R)), int(round(self.GY / self.R))
        self.NXB, self.NYB = int(math.ceil(L_max / self.R)), int(math.floor(W / self.R))
        self._desf = {}
        self._construir_tipos()

    # ---- conjuntos M, q_m y T
    def _construir_tipos(self):
        self.TIPOS, self.Q, self.AREA, self.GRUPO = [], {}, {}, {}
        self.NOMBRE = {}                                  # nombre técnico de cada molde (para la leyenda)
        for m, g in piezas_de_talla(self.df, self.talla):
            g = g.sort_values('vertice')
            if 'nombre' in g and pd.notna(g['nombre'].iloc[0]) and str(g['nombre'].iloc[0]).strip():
                self.NOMBRE[m] = str(g['nombre'].iloc[0]).strip()
            base = graduar(g[['x_cm', 'y_cm']].to_numpy(float), self.talla, self.P, _gradua(g))
            n = int(g['n_por_pantalon'].iloc[0])
            espejo = 'par_espejo' in g and int(g['par_espejo'].iloc[0]) == 1 and n % 2 == 0
            variantes = [(m, base, n)] if not espejo else \
                [(m + ' izq', base, n // 2), (m + ' der', base * np.array([1, -1]), n // 2)]
            for nombre, pol, cant in variantes:
                self.AREA[nombre] = area_poligono(pol)
                self.Q[nombre] = self.K * cant
                self.GRUPO[nombre] = g['grupo'].iloc[0]
                con_h = desplazar_contorno(pol, self.P['holgura_cm'] / 2) if self.P['holgura_cm'] > 0 else pol
                angulos = [0, 180] + ([90, 270] if int(g['permite_90'].iloc[0]) == 1 else [])
                vistos = []
                for a in angulos:
                    pts = rotar(con_h, a)
                    mk = mascara(pts, self.R)
                    if any(mk.shape == v.shape and (mk == v).all() for v in vistos):
                        continue
                    vistos.append(mk)
                    self.TIPOS.append(dict(molde=nombre, ang=a, pts=pts, w=pts[:, 0].max(),
                                           h=pts[:, 1].max(), mask=mk))
        self.MOLDES = list(self.Q)
        self.T_m = {m: [t for t, tp in enumerate(self.TIPOS) if tp['molde'] == m] for m in self.MOLDES}

    # ---- puntos factibles y conflictos
    def puntos_factibles(self, t, x_ini, x_fin, ocupado):
        tp = self.TIPOS[t]
        I = [i for i in range(int(math.ceil(x_ini / self.GX)), int(self.L_MAX // self.GX) + 1)
             if i * self.GX + tp['w'] <= min(x_fin, self.L_MAX) + 1e-9]
        J = [j for j in range(int(self.W // self.GY) + 1) if j * self.GY + tp['h'] <= self.W + 1e-9]
        if not I or not J:
            return []
        if ocupado is None or not ocupado.any():
            return [(i, j) for i in I for j in J]
        choque = correlate(ocupado.astype(float), tp['mask'].astype(float), mode='valid')
        return [(i, j) for i in I for j in J
                if i * self.SX < choque.shape[0] and j * self.SY < choque.shape[1]
                and choque[i * self.SX, j * self.SY] < 0.5]

    def desfases(self, t, u):
        if (t, u) not in self._desf:
            A, B = self.TIPOS[t]['mask'].astype(float), self.TIPOS[u]['mask'].astype(float)
            C = correlate(A, B, mode='full', method='fft')
            ox = np.arange(C.shape[0]) - (B.shape[0] - 1)
            oy = np.arange(C.shape[1]) - (B.shape[1] - 1)
            ii, jj = np.nonzero(C > 0.5)
            dx, dy = ox[ii], oy[jj]
            sel = (dx % self.SX == 0) & (dy % self.SY == 0)
            self._desf[(t, u)] = list(zip((dx[sel] // self.SX).tolist(), (dy[sel] // self.SY).tolist()))
        return self._desf[(t, u)]

    def _pares(self, tipos_b, D):
        pares = 0
        for a, t in enumerate(tipos_b):
            for u in tipos_b[a:]:
                Su = set(D[u])
                off = [o for o in self.desfases(t, u) if not (t == u and o == (0, 0))]
                c = sum(1 for d in D[t] for (ox, oy) in off if (d[0] + ox, d[1] + oy) in Su)
                pares += c // 2 if t == u else c
        return pares

    # ---- modelo de un bloque: (1) a (6)
    def _resolver_bloque(self, tipos_b, D, piezas, L_inf, area_previa):
        prob = pulp.LpProblem('tizado', pulp.LpMinimize)
        z = {(t, d): pulp.LpVariable(f'z_{t}_{d[0]}_{d[1]}', cat=pulp.LpBinary) for t in tipos_b for d in D[t]}
        L = pulp.LpVariable('L', lowBound=L_inf, upBound=self.L_MAX)               # (5)
        prob += L                                                                    # (1)
        for m, cant in piezas.items():                                               # (2)
            prob += pulp.lpSum(z[t, d] for t in self.T_m[m] for d in D[t]) == cant
        for (t, d), v in z.items():                                                  # (3)
            prob += L >= (d[0] * self.GX + self.TIPOS[t]['w']) * v
        pares = self._pares(tipos_b, D)
        forma = 'pares' if pares <= self.P['max_pares'] else 'agregada'
        for a, t in enumerate(tipos_b):                                              # (4)
            for u in (tipos_b[a:] if forma == 'pares' else tipos_b):
                Su = set(D[u])
                for d in D[t]:
                    C = [(d[0] + ox, d[1] + oy) for (ox, oy) in self.desfases(t, u) if (d[0] + ox, d[1] + oy) in Su]
                    if t == u:
                        C = [e for e in C if e != d]
                    if not C:
                        continue
                    if forma == 'pares':
                        for e in C:
                            if t != u or e > d:
                                prob += z[t, d] + z[u, e] <= 1
                    else:
                        prob += len(C) * z[t, d] + pulp.lpSum(z[u, e] for e in C) <= len(C)
        prob += L >= (area_previa + sum(c * self.AREA[m] for m, c in piezas.items())) / self.W   # (6)
        import os, re, tempfile
        log = os.path.join(tempfile.gettempdir(), f'cbc_{os.getpid()}_{id(prob)}.log')
        semilla = int(self.P.get('semilla', 1))
        solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=self.P['tiempo_limite_s'], gapRel=self.P['gap_rel'],
                                   logPath=log, options=[f'randomCbcSeed {semilla}'])
        t0 = time.time()
        prob.solve(solver)
        seg = time.time() - t0
        ok = (prob.status == 1 or getattr(prob, 'sol_status', 0) in (1, 2)) and \
            all(v.varValue is not None for v in z.values())
        # gap del bloque, leído del registro de CBC
        texto = open(log, encoding='utf-8', errors='ignore').read() if os.path.exists(log) else ''
        gap = None
        if 'Optimal solution found' in texto:
            gap = 0.0
        else:
            m_obj = re.search(r'^Objective value:\s*([-\d.eE+]+)', texto, re.M)
            m_lb = re.search(r'^Lower bound:\s*([-\d.eE+]+)', texto, re.M)
            if m_obj and m_lb and float(m_obj.group(1)) > 0:
                gap = 100 * (float(m_obj.group(1)) - float(m_lb.group(1))) / float(m_obj.group(1))
        return ok, z, L, seg, forma, gap

    def resolver(self, progreso=None):
        """Procedimiento por bloques: grandes (un molde por bloque), luego pequeñas en los huecos."""
        grandes = [m for m in self.MOLDES if self.GRUPO[m] == 'G']
        pequenas = sorted([m for m in self.MOLDES if self.GRUPO[m] == 'P'], key=lambda m: -self.AREA[m])
        largo_g = max(self.TIPOS[t]['w'] for m in grandes for t in self.T_m[m]) if grandes else 0
        bloques = [dict(nombre=f'{m} {p + 1}', piezas={m: self.Q[m] // self.K}, auto=True)
                   for p in range(self.K) for m in grandes]
        bloques += [dict(nombre=m, piezas={m: self.Q[m]}, auto=False) for m in pequenas]
        ocupado = np.zeros((self.NXB, self.NYB), dtype=bool)
        colocadas, L_act, area_prev, seg_total = [], 0.0, 0.0, 0.0
        self.bloques_info = []
        for k, b in enumerate(bloques):
            tipos_b = [t for m in b['piezas'] for t in self.T_m[m]]
            x_ini = max(0, L_act - largo_g) if b['auto'] else 0
            x_fin = x_ini + largo_g + self.P['margen_ventana_cm'] if b['auto'] else L_act
            while True:
                D = {t: self.puntos_factibles(t, x_ini, x_fin, ocupado) for t in tipos_b}
                ok = False
                if sum(len(v) for v in D.values()) >= sum(b['piezas'].values()):
                    ok, z, L, seg, forma, gap = self._resolver_bloque(tipos_b, D, b['piezas'], L_act, area_prev)
                    seg_total += seg
                if ok or x_fin >= self.L_MAX:
                    break
                x_fin = min(self.L_MAX, x_fin + max(largo_g, 20) / 2)
            if not ok:
                raise RuntimeError(f'El bloque "{b["nombre"]}" no cabe en el largo de la mesa.')
            for (t, d), v in z.items():
                if v.varValue > 0.5:
                    mk = self.TIPOS[t]['mask']
                    x, y = d[0] * self.SX, d[1] * self.SY
                    ocupado[x:x + mk.shape[0], y:y + mk.shape[1]] |= mk
                    colocadas.append((t, d))
            L_act = max(L_act, pulp.value(L))
            area_prev += sum(c * self.AREA[m] for m, c in b['piezas'].items())
            self.bloques_info.append(dict(bloque=b['nombre'], segundos=round(seg, 1),
                                          gap_pct=None if gap is None else round(gap, 2),
                                          largo_cm=round(L_act, 1)))
            if progreso:
                progreso((k + 1) / len(bloques), f'Bloque {k + 1} de {len(bloques)}: {b["nombre"]}')
        self.colocadas, self.L = colocadas, L_act
        self.segundos = seg_total
        self.cota = area_prev / self.W                                   # cota inferior (6) del tizado completo
        self.dist_cota = 100 * (L_act - self.cota) / L_act if L_act > 0 else None
        gaps = [b['gap_pct'] for b in self.bloques_info if b['gap_pct'] is not None]
        self.gap_max = max(gaps) if gaps else None
        return colocadas, L_act

    def verificar_poligonos(self, tol_cm2=0.5):
        """Verificación independiente con los polígonos reales (no con la rejilla de celdas)."""
        from shapely.geometry import Polygon
        pols = [Polygon(self.TIPOS[t]['pts'] + [d[0] * self.GX, d[1] * self.GY]).buffer(0)
                for t, d in self.colocadas]
        cajas = [p.bounds for p in pols]
        choques, peor = 0, 0.0
        for i in range(len(pols)):
            for j in range(i + 1, len(pols)):
                a1, b1, c1, d1 = cajas[i]
                a2, b2, c2, d2 = cajas[j]
                if c1 <= a2 or c2 <= a1 or d1 <= b2 or d2 <= b1:
                    continue                                   # sus rectángulos no se tocan
                a = pols[i].intersection(pols[j]).area
                peor = max(peor, a)
                if a > tol_cm2:
                    choques += 1
        dentro = all(p.bounds[1] >= -1e-6 and p.bounds[3] <= self.W + 1e-6 for p in pols)
        return dict(superposiciones=choques, mayor_superposicion_cm2=round(peor, 2), dentro_de_la_tela=dentro)

    def verificar(self):
        """Verificación independiente (punto de control 3)."""
        cuenta = np.zeros((self.NXB, self.NYB), dtype=int)
        for t, d in self.colocadas:
            mk = self.TIPOS[t]['mask']
            x, y = d[0] * self.SX, d[1] * self.SY
            cuenta[x:x + mk.shape[0], y:y + mk.shape[1]] += mk
        piezas_ok = all(sum(1 for t, d in self.colocadas if self.TIPOS[t]['molde'] == m) == self.Q[m]
                        for m in self.MOLDES)
        return dict(sin_superposicion=bool(cuenta.max() <= 1), piezas_completas=piezas_ok,
                    dentro_de_la_tela=all(d[1] * self.GY + self.TIPOS[t]['h'] <= self.W + 1e-6
                                          for t, d in self.colocadas))

    def dibujar(self, titulo=''):
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as MplPolygon
        fig, ax = plt.subplots(figsize=(14, max(3, min(9, 14 * self.W / max(self.L, 1)))))
        ax.add_patch(plt.Rectangle((0, 0), self.L, self.W, fc='#fbe9e7', ec='k'))
        base = list(dict.fromkeys(m.replace(' izq', '').replace(' der', '') for m in self.MOLDES))   # piezas grandes primero
        col = dict(zip(base, plt.cm.tab20(np.linspace(0, 1, max(len(base), 2)))))
        for t, d in self.colocadas:
            tp = self.TIPOS[t]
            nombre = tp['molde'].replace(' izq', '').replace(' der', '')
            pol = tp['pts'] + [d[0] * self.GX, d[1] * self.GY]
            ax.add_patch(MplPolygon(pol, fc=col[nombre], ec='k', lw=0.6))
            # letra de la pieza, en el punto interior más alejado del borde
            etiqueta = _codigo(nombre)
            cx, cy = _punto_interior(pol)
            lado = min(tp['w'], tp['h'])
            ax.text(cx, cy, etiqueta, ha='center', va='center', fontsize=8 if lado >= 8 else 5.5,
                    weight='bold', color='#1a1a1a', clip_on=True)
        handles = [plt.Rectangle((0, 0), 1, 1, fc=col[n], ec='k', lw=0.6) for n in base]
        textos = [f'{_codigo(n)} · {self.NOMBRE.get(n, n)}' for n in base]
        ax.legend(handles, textos, loc='upper center', bbox_to_anchor=(0.5, -0.1), ncol=min(4, len(base)),
                  fontsize=9, title='Piezas del tizado', title_fontsize=9, frameon=False)
        ax.set_xlim(0, self.L * 1.01)
        ax.set_ylim(0, self.W)
        ax.set_aspect('equal')
        ax.set_xlabel('Largo (cm)')
        ax.set_ylabel('Ancho útil (cm)')
        ax.set_title(titulo or f'Talla {self.talla}: largo {self.L:.1f} cm, ancho útil {self.W:.0f} cm')
        fig.tight_layout()
        return fig


def _codigo(molde):
    """Código corto que se escribe sobre cada pieza (la letra, o una abreviatura para presilla y pretina)."""
    especiales = {'presilla': 'Ps', 'pretina': 'Pt'}
    return especiales.get(molde.lower(), molde if len(molde) <= 3 else molde[:3].capitalize())


def _punto_interior(pol):
    """Punto dentro de la pieza y lejos de su borde, para escribir la letra (sirve también en piezas curvas)."""
    xmin, ymin = pol.min(axis=0)
    xmax, ymax = pol.max(axis=0)
    xs, ys = np.linspace(xmin, xmax, 25), np.linspace(ymin, ymax, 25)
    XX, YY = np.meshgrid(xs, ys)
    P = np.c_[XX.ravel(), YY.ravel()]
    dentro = Path(pol).contains_points(P)
    if not dentro.any():
        return pol.mean(axis=0)
    P = P[dentro]
    a, b = pol, np.roll(pol, -1, axis=0)                      # distancia de cada punto al borde más cercano
    ab = b - a
    t = np.clip(((P[:, None, :] - a) * ab).sum(-1) / np.maximum((ab ** 2).sum(-1), 1e-9), 0, 1)
    dist = np.linalg.norm(P[:, None, :] - (a + t[..., None] * ab), axis=-1).min(axis=1)
    return P[dist.argmax()]


# ----------------------------------------------------------------------------- plan de corte
def plan_de_corte(orden, escenario, K_por_talla, capas_max):
    """
    Devuelve los grupos de capas (tendidos).
    'actual': todo el pedido con el tizado de la talla mayor (luego se recortan las tallas menores).
    'por_talla': cada talla con su propio tizado.
    K_por_talla: pantalones por capa que caben en el tizado de cada talla.
    """
    orden = {int(s): int(q) for s, q in orden.items() if int(q) > 0}
    grupos = []
    if escenario == 'actual':
        tmax = max(orden)
        k = K_por_talla[tmax]
        capas = math.ceil(sum(orden.values()) / k)
        for i in range(math.ceil(capas / capas_max)):
            c = min(capas_max, capas - i * capas_max)
            grupos.append(dict(grupo=f'Tendido {i + 1}', talla_tizado=tmax, k=k, capas=c))
    else:
        for s in sorted(orden, reverse=True):
            k = K_por_talla[s]
            capas = math.ceil(orden[s] / k)
            for i in range(math.ceil(capas / capas_max)):
                c = min(capas_max, capas - i * capas_max)
                grupos.append(dict(grupo=f'Talla {s} · tendido {i + 1}', talla_tizado=s, k=k, capas=c))
    return grupos


def tela_derivados(tela):
    """Gramaje (kg/m²) y precio por m² a partir del peso y el precio por metro lineal."""
    ancho_m = tela['ancho_referencia_cm'] / 100
    return tela['peso_kg_por_metro'] / ancho_m, tela['precio_soles_por_metro'] / ancho_m


def indicadores_pedido(orden, grupos, largos, area_pant, tela, ancho_bruto, P):
    """
    Merma del pedido en m², kg, soles y %.
    largos: {talla_tizado: L en cm};  area_pant: {talla: cm² de un pantalón}.
    """
    A_bruto = ancho_bruto
    W = A_bruto - P['perdida_ancho_cm']
    gramaje, precio_m2 = tela_derivados(tela)
    orden = {int(s): int(q) for s, q in orden.items() if int(q) > 0}
    tmax = max(orden)
    filas = []
    for g in grupos:
        L = largos[g['talla_tizado']]
        tela_m2 = A_bruto * (L + P['perdida_largo_cm']) * g['capas'] / 1e4
        util_tizado_m2 = W * L * g['capas'] / 1e4
        piezas_m2 = g['k'] * area_pant[g['talla_tizado']] * g['capas'] / 1e4
        filas.append(dict(grupo=g['grupo'], talla_tizado=g['talla_tizado'], pantalones_por_capa=g['k'],
                          capas=g['capas'], largo_tizado_m=round(L / 100, 3),
                          metros_de_tela=round((L + P['perdida_largo_cm']) * g['capas'] / 100, 2),
                          tela_m2=tela_m2, bordes_m2=tela_m2 - util_tizado_m2,
                          planificacion_m2=util_tizado_m2 - piezas_m2))
    G = pd.DataFrame(filas)
    recorte_m2 = 0.0
    if all(g['talla_tizado'] == tmax for g in grupos) and len(orden) > 1:
        recorte_m2 = sum(q * (area_pant[tmax] - area_pant[s]) for s, q in orden.items()) / 1e4
    tela_total = G['tela_m2'].sum()
    comp = {'Planificación (entre piezas)': G['planificacion_m2'].sum(),
            'Recorte de tallas': recorte_m2,
            'Bordes y extremos': G['bordes_m2'].sum()}
    comp['Total'] = sum(comp.values())
    M = pd.DataFrame([dict(componente=k, m2=v, kg=v * gramaje, soles=v * precio_m2,
                           porcentaje=100 * v / tela_total) for k, v in comp.items()])
    resumen = dict(tela_m2=tela_total, tela_kg=tela_total * gramaje, tela_soles=tela_total * precio_m2,
                   metros_lineales=G['metros_de_tela'].sum(), gramaje=gramaje,
                   pantalones_cortados=int((G['capas'] * G['pantalones_por_capa']).sum()),
                   pantalones_pedidos=sum(orden.values()))
    return G, M, resumen
