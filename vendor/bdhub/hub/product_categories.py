"""BDHub商品一级类目规范化与可审计标题规则。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping


CATEGORY_RULE_VERSION = "mx-canonical-v1"
AI_CATEGORY_METHOD = "ai_classified"
AI_CATEGORY_RULE_VERSION = "mx-codex-ai-v1"
UNCLASSIFIED_CATEGORY = "待归类"

CANONICAL_CATEGORIES = (
    "女装与女士内衣",
    "穆斯林时尚",
    "时尚配件",
    "运动与户外",
    "家纺布艺",
    "美妆个护",
    "手机与数码",
    "宠物用品",
    "玩具和爱好",
    "食品饮料",
    "母婴用品",
    "保健",
    "二手",
    "男装与男士内衣",
    "收藏品",
    "箱包",
    "居家日用",
    "厨房用品",
    "家具",
    "五金工具",
    "虚拟商品",
    "家装建材",
    "家电",
    "珠宝与衍生品",
    "汽车与摩托车",
    "图书&杂志&音频",
    "鞋靴",
    "儿童时尚",
    "电脑办公",
)


@dataclass(frozen=True)
class CategoryDecision:
    category: str
    method: str
    rule_version: str = CATEGORY_RULE_VERSION


def normalize_category_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", text, flags=re.UNICODE).strip()


def _aliases(*values: str) -> tuple[str, ...]:
    return tuple(values)


_CATEGORY_ALIAS_GROUPS: Mapping[str, tuple[str, ...]] = {
    "女装与女士内衣": _aliases(
        "女装与女士内衣", "女装", "Womenswear & Underwear", "Women's Fashion",
    ),
    "穆斯林时尚": _aliases("穆斯林时尚", "Muslim Fashion"),
    "时尚配件": _aliases(
        "时尚配件", "Fashion Accessories", "服饰配件", "Clothes Accessories",
    ),
    "运动与户外": _aliases(
        "运动与户外", "运动户外", "Sports & Outdoor", "Sports and Outdoor",
    ),
    "家纺布艺": _aliases("家纺布艺", "Textiles & Soft Furnishings"),
    "美妆个护": _aliases(
        "美妆个护", "Beauty & Personal Care", "Personal Care", "个护电器",
    ),
    "手机与数码": _aliases(
        "手机与数码", "手机电子", "Phones & Electronics", "Phone & Electronics",
    ),
    "宠物用品": _aliases("宠物用品", "Pet Supplies"),
    "玩具和爱好": _aliases("玩具和爱好", "Toys & Hobbies", "Toys and Hobbies"),
    "食品饮料": _aliases("食品饮料", "Food & Beverages", "Food and Beverages"),
    "母婴用品": _aliases(
        "母婴用品", "Mother & Baby", "Baby & Maternity", "Maternity & Baby",
    ),
    "保健": _aliases("保健", "健康保健", "Health", "Health Care"),
    "二手": _aliases("二手", "Pre-Owned", "Secondhand", "Second Hand"),
    "男装与男士内衣": _aliases(
        "男装与男士内衣", "男装", "Menswear & Underwear", "Men's Fashion",
    ),
    "收藏品": _aliases("收藏品", "Collectibles"),
    "箱包": _aliases("箱包", "Luggage & Bags", "Bags & Luggage"),
    "居家日用": _aliases("居家日用", "家居日用", "Home Supplies"),
    "厨房用品": _aliases("厨房用品", "Kitchenware", "Kitchen Supplies"),
    "家具": _aliases("家具", "Furniture"),
    "五金工具": _aliases("五金工具", "Tools & Hardware", "Tools and Hardware"),
    "虚拟商品": _aliases("虚拟商品", "Virtual Products", "Virtual Goods"),
    "家装建材": _aliases("家装建材", "Home Improvement"),
    "家电": _aliases("家电", "家用电器", "Household Appliances", "Home Appliances"),
    "珠宝与衍生品": _aliases(
        "珠宝与衍生品", "珠宝配饰", "Jewelry & Derivatives",
        "Jewellery & Derivatives", "Jewelry Accessories & Derivatives",
    ),
    "汽车与摩托车": _aliases(
        "汽车与摩托车", "汽车用品", "Automotive & Motorcycle",
        "Automotive and Motorcycle",
    ),
    "图书&杂志&音频": _aliases(
        "图书&杂志&音频", "图书文具", "Books, Magazines & Audio",
        "Books Magazines and Audio",
    ),
    "鞋靴": _aliases("鞋靴", "Shoes", "Footwear"),
    "儿童时尚": _aliases("儿童时尚", "Kids' Fashion", "Kids Fashion"),
    "电脑办公": _aliases(
        "电脑办公", "电脑&办公文具", "Computers & Office Equipment",
        "Computers and Office Equipment",
    ),
}

CATEGORY_ALIASES = {
    normalize_category_text(alias): category
    for category, aliases in _CATEGORY_ALIAS_GROUPS.items()
    for alias in aliases
}


def _category_candidates(value: object) -> Iterable[str]:
    raw = str(value or "").strip()
    if not raw:
        return ()
    values = [raw]
    values.extend(
        part.strip()
        for part in re.split(r"\r?\n|\s+/\s+|\s+\|\s+", raw)
        if part.strip()
    )
    return tuple(dict.fromkeys(values))


def category_from_alias(*values: object) -> str | None:
    for value in values:
        for candidate in _category_candidates(value):
            mapped = CATEGORY_ALIASES.get(normalize_category_text(candidate))
            if mapped:
                return mapped
    return None


def _full_managed_category(value: object) -> str | None:
    candidates = tuple(_category_candidates(value))
    if not candidates:
        return None
    for candidate in candidates:
        if re.search(r"[\u4e00-\u9fff]", candidate):
            return category_from_alias(candidate) or candidate.strip()
    return category_from_alias(*candidates) or candidates[0].strip()


def _words_pattern(*values: str) -> re.Pattern[str]:
    return re.compile(rf"(?:^|\s)(?:{'|'.join(values)})(?:\s|$)")


# 只使用可解释的商品本体词。规则顺序代表“更具体的本体优先”。
_TITLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("虚拟商品", _words_pattern(
        r"tarjeta de regalo", r"gift card", r"codigo digital", r"recarga digital",
        r"suscripcion digital", r"producto virtual",
    )),
    ("二手", _words_pattern(
        r"segunda mano", r"pre owned", r"usado", r"reacondicionado", r"refurbished",
    )),
    ("收藏品", _words_pattern(
        r"coleccionable", r"coleccionables", r"figura de coleccion", r"tarjetas coleccionables",
        r"monedas coleccionables", r"sellos coleccionables", r"memorabilia",
    )),
    ("宠物用品", _words_pattern(
        r"para mascotas", r"para perros", r"para gatos", r"perros y gatos",
        r"arena para gato", r"correa para perro", r"comedero para mascotas",
        r"collar para perro", r"collar para gato", r"juguete para perro", r"juguete para gato",
    )),
    ("食品饮料", _words_pattern(
        r"snack", r"snacks", r"dulce", r"dulces", r"chocolate", r"chocolates",
        r"caramelo", r"galletas", r"cafe", r"matcha", r"ramen", r"fideos",
        r"salsa", r"condimento", r"botana", r"bebida", r"te verde", r"te negro",
    )),
    ("图书&杂志&音频", _words_pattern(
        r"libro", r"libros", r"novela", r"revista", r"audiolibro", r"album musical",
        r"vinilo musical", r"cd musical",
    )),
    ("汽车与摩托车", _words_pattern(
        r"automovil", r"automotriz", r"cargador de auto", r"soporte para auto",
        r"soporte para coche", r"para motocicleta", r"parabrisas", r"limpiaparabrisas",
        r"funda para volante", r"accesorio para auto", r"neumatico", r"llanta",
        r"asiento de auto", r"lavado de autos", r"car wash", r"dash cam", r"autoestereo",
        r"cubierta para auto", r"funda para coche", r"funda para auto", r"portamonedas .* auto",
    )),
    ("五金工具", _words_pattern(
        r"herramienta", r"herramientas", r"destornillador", r"desarmador", r"taladro",
        r"broca", r"alicate", r"martillo", r"sierra", r"soldador", r"soldadura",
        r"tornillo", r"tuerca", r"llave inglesa", r"llave de impacto", r"multimetro",
        r"compresor", r"hidrolavadora", r"caja de herramientas", r"amoladora", r"esmeril",
        r"soplador", r"soplador de aire", r"cortador profesional",
    )),
    ("家装建材", _words_pattern(
        r"plomeria", r"grifo", r"lavabo", r"papel tapiz", r"panel de pared",
        r"paneles de pared", r"suelo vinilico", r"piso vinilico", r"material de construccion",
        r"cerradura", r"bisagra", r"nivel laser", r"cinta metrica", r"papel pintado",
        r"vinilo autoadhesivo", r"azulejo", r"azulejos", r"pegatina para suelo",
        r"pelicula para ventana", r"pelicula para ventanas", r"lampara", r"lamparas",
        r"plafon", r"luces led", r"malla de sombra",
    )),
    ("家具", _words_pattern(
        r"mueble", r"muebles", r"mesa", r"mesas", r"silla", r"sillas", r"sofa",
        r"sofas", r"escritorio", r"escritorios", r"estante", r"estantes", r"armario",
        r"gabinete", r"librero", r"mesita de noche",
    )),
    ("家电", _words_pattern(
        r"electrodomestico", r"lavadora", r"secadora de ropa", r"aspiradora", r"licuadora",
        r"cafetera", r"freidora", r"air fryer", r"ventilador", r"calefactor", r"humidificador",
        r"purificador de aire", r"maquina de coser", r"parrilla electrica", r"plancha de vapor",
    )),
    ("家纺布艺", _words_pattern(
        r"almohada", r"almohadas", r"cojin", r"cojines", r"manta", r"mantas",
        r"frazada", r"toalla", r"toallas", r"cortina", r"cortinas", r"alfombra",
        r"alfombras", r"sabana", r"sabanas", r"edredon", r"funda de almohada",
        r"protector de colchon", r"mantel", r"manteles",
    )),
    ("厨房用品", _words_pattern(
        r"vajilla", r"vaso", r"vasos", r"taza", r"tazas", r"termo", r"termos",
        r"sarten", r"sartenes", r"olla", r"ollas", r"cuchillo", r"cuchillos",
        r"tabla de cortar", r"recipiente de cocina", r"utensilio de cocina", r"cubiertos",
        r"colador", r"coladores", r"especiero", r"fiambrera", r"lonchera termica",
    )),
    ("居家日用", _words_pattern(
        r"organizador", r"organizadores", r"limpieza", r"escoba", r"trapeador",
        r"recipiente de almacenamiento", r"decoracion del hogar", r"para el hogar",
        r"bano", r"dormitorio", r"papel higienico", r"bolsa de basura", r"gancho",
        r"ganchos", r"percha", r"perchas", r"decoracion .* hogar", r"decoracion .* casa",
    )),
    ("玩具和爱好", _words_pattern(
        r"juguete", r"juguetes", r"muneca", r"munecas", r"peluche", r"peluches",
        r"rompecabezas", r"puzzle", r"bloques de construccion", r"juego de mesa",
        r"juego educativo", r"squishy", r"antiestres", r"modelismo", r"manualidades",
        r"scrapbooking", r"globo", r"globos", r"calibrador de globos",
    )),
    ("母婴用品", _words_pattern(
        r"bebe", r"bebes", r"recien nacido", r"carriola", r"cochecito", r"panal",
        r"panales", r"lactancia", r"biberon", r"biberones", r"mordedera", r"cuna",
        r"maternidad", r"embarazo",
    )),
    ("儿童时尚", _words_pattern(
        r"ropa infantil", r"ropa para ninos", r"ropa para ninas", r"vestido de nina",
        r"camisa de nino", r"pantalon de nino", r"moda infantil",
    )),
    ("美妆个护", _words_pattern(
        r"maquillaje", r"cosmetico", r"cosmeticos", r"labial", r"labios", r"lip gloss",
        r"pestana", r"pestanas",
        r"ceja", r"mascara", r"rubor", r"base facial", r"corrector", r"delineador",
        r"sombra de ojos", r"manicura", r"skincare", r"cuidado de la piel", r"serum",
        r"crema facial", r"protector solar", r"perfume", r"fragancia", r"cabello", r"peluca",
        r"shampoo", r"acondicionador", r"secador de cabello", r"depilador", r"afeitadora",
        r"barba", r"mascarilla facial", r"retinol", r"pigmento", r"pigmentos",
        r"unas", r"esmalte", r"polygel", r"nails", r"polvo suelto", r"crema para pies",
        r"recortador nasal", r"eau de toilette", r"edp spray", r"plancha infrarroja",
    )),
    ("保健", _words_pattern(
        r"suplemento", r"suplementos", r"vitamina", r"vitaminas", r"colageno", r"creatina",
        r"proteina", r"capsula", r"capsulas", r"minerales", r"probiotico", r"omega 3",
        r"bienestar", r"oximetro", r"glucometro", r"presion arterial", r"ortopedico",
        r"cepillo dental", r"cepillo de dientes", r"pasta dental", r"higiene oral",
        r"corrector de postura", r"inositol", r"equilibrio hormonal",
    )),
    ("电脑办公", _words_pattern(
        r"computadora", r"computadoras", r"laptop", r"laptops", r"monitor para pc",
        r"teclado", r"teclados", r"mouse", r"raton", r"impresora", r"impresoras",
        r"memoria usb", r"disco duro", r"ssd", r"webcam", r"papeleria", r"cuaderno",
        r"agenda", r"lapiz", r"lapices", r"boligrafo", r"marcador", r"marcadores",
        r"material escolar", r"album de fotos", r"albumes de fotos", r"laminado para fotos",
    )),
    ("手机与数码", _words_pattern(
        r"telefono", r"telefonos", r"celular", r"celulares", r"smartphone", r"iphone",
        r"ipad", r"samsung", r"funda para iphone", r"funda para celular", r"vidrio templado",
        r"audifono", r"audifonos", r"auricular", r"earbuds", r"bluetooth", r"cargador",
        r"cable usb", r"carga rapida", r"power bank", r"smartwatch", r"reloj inteligente",
        r"bocina", r"altavoz", r"microfono", r"tablet", r"android", r"nintendo",
        r"gamepad", r"camara digital", r"proyector", r"gps", r"drone",
    )),
    ("鞋靴", _words_pattern(
        r"zapato", r"zapatos", r"zapatilla", r"zapatillas", r"tenis", r"sneaker",
        r"sandalia", r"sandalias", r"bota", r"botas", r"botin", r"tacon", r"calzado",
        r"mocasin", r"chancla", r"pantufla", r"huarache", r"plantilla", r"plantillas",
    )),
    ("箱包", _words_pattern(
        r"bolso", r"bolsos", r"bolsa de mano", r"mochila", r"mochilas", r"cartera",
        r"maleta", r"maletas", r"equipaje", r"cangurera", r"tote bag", r"monedero",
        r"portafolio", r"bolsa cruzada", r"bandolera", r"neceser", r"bolsa de viaje",
        r"bolsa tote", r"lonchera .* bolsa",
    )),
    ("珠宝与衍生品", _words_pattern(
        r"joya", r"joyas", r"joyeria", r"collar", r"collares", r"arete", r"aretes",
        r"pendiente", r"pulsera", r"pulseras", r"anillo", r"anillos", r"brazalete",
        r"piercing",
    )),
    ("时尚配件", _words_pattern(
        r"gafas", r"lentes de sol", r"sombrero", r"sombreros", r"gorra", r"gorras",
        r"cinturon", r"cinturones", r"bufanda", r"bufandas", r"reloj de moda", r"diadema",
        r"pasador", r"broche", r"accesorio para el cabello", r"paraguas", r"llavero",
    )),
    ("穆斯林时尚", _words_pattern(
        r"hijab", r"abaya", r"kaftan", r"burka", r"niqab", r"ropa musulmana",
    )),
    ("运动与户外", _words_pattern(
        r"gimnasio", r"fitness", r"yoga", r"deporte", r"deportes", r"deportivo",
        r"deportiva", r"deportivos", r"deportivas",
        r"ejercicio", r"ciclismo", r"bicicleta", r"camping", r"senderismo", r"pesca",
        r"running", r"pesa", r"mancuerna", r"futbol", r"baloncesto", r"voleibol",
        r"natacion", r"piscina", r"outdoor", r"aire libre", r"carpa", r"cuerda para saltar",
    )),
    ("女装与女士内衣", _words_pattern(
        r"vestido", r"vestidos", r"falda", r"faldas", r"blusa", r"blusas", r"brasier",
        r"sujetador", r"lenceria", r"bikini", r"traje de bano", r"legging", r"braga",
        r"panties", r"tanga", r"faja", r"corse", r"corset", r"ropa de mujer",
        r"ropa femenina", r"para mujer", r"para mujeres", r"de mujer", r"de mujeres",
        r"crop top", r"bodysuit", r"camisetas?.*(?:mujer|mujeres)",
        r"playeras?.*(?:mujer|mujeres)", r"pantalones?.*(?:mujer|mujeres)",
        r"shorts?.*(?:mujer|mujeres)", r"conjunto.*(?:mujer|mujeres)",
    )),
    ("男装与男士内衣", _words_pattern(
        r"ropa de hombre", r"ropa masculina", r"para hombre", r"para hombres", r"de hombre",
        r"de hombres", r"para caballero",
        r"pantalon cargo", r"pantalon tactico", r"boxer", r"camisa de hombre",
        r"camisetas?.*(?:hombre|hombres)", r"playeras?.*(?:hombre|hombres)",
        r"pantalones?.*(?:hombre|hombres)", r"shorts?.*(?:hombre|hombres)",
    )),
)


def category_from_title(value: object) -> str | None:
    normalized = normalize_category_text(value)
    if not normalized:
        return None
    for category, pattern in _TITLE_RULES:
        if pattern.search(normalized):
            return category
    return None


def classify_product_category(
    *,
    pool_key: str,
    category_l1: object = None,
    category_l2: object = None,
    category_l3: object = None,
    product_name: object = None,
    source_attributes: Mapping[str, object] | None = None,
) -> CategoryDecision:
    clean_pool = str(pool_key or "").strip().lower()
    if clean_pool == "full_managed":
        category = _full_managed_category(category_l1)
        return CategoryDecision(
            category=category or UNCLASSIFIED_CATEGORY,
            method="source_full_managed" if category else "unclassified",
        )
    mapped = category_from_alias(category_l1, category_l2, category_l3)
    if mapped:
        return CategoryDecision(category=mapped, method="source_alias")
    inferred = category_from_title(product_name)
    if inferred:
        return CategoryDecision(category=inferred, method="title_rule")
    return CategoryDecision(category=UNCLASSIFIED_CATEGORY, method="unclassified")


__all__ = [
    "CANONICAL_CATEGORIES",
    "AI_CATEGORY_METHOD",
    "AI_CATEGORY_RULE_VERSION",
    "CATEGORY_ALIASES",
    "CATEGORY_RULE_VERSION",
    "CategoryDecision",
    "UNCLASSIFIED_CATEGORY",
    "category_from_alias",
    "category_from_title",
    "classify_product_category",
    "normalize_category_text",
]
