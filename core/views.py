from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from core.forms import AperturaCajaForm, CierreCajaForm, DivisaForm, MovimientoTesoreriaForm
from security.permissions import AREA_TESORERIA, area_requerida
from treasury.models import CorteCaja, Divisa, MovimientoDivisa, MovimientoTesoreria, SaldoCaja


@area_requerida(AREA_TESORERIA)
def index(request):
    ultimo_corte = CorteCaja.objects.order_by('-fecha').first()
    saldos = ultimo_corte.saldos.select_related('divisa') if ultimo_corte else []
    movimientos = MovimientoTesoreria.objects.prefetch_related('divisas__divisa').order_by('-fecha', '-creado')

    q_fecha = request.GET.get('fecha', '').strip()
    q_folio = request.GET.get('folio', '').strip()
    q_tipo = request.GET.get('tipo', '').strip()
    q_divisa = request.GET.get('divisa', '').strip()
    q_beneficiario = request.GET.get('beneficiario', '').strip()

    if q_fecha:
        movimientos = movimientos.filter(fecha=q_fecha)
    if q_folio:
        movimientos = movimientos.filter(folio__icontains=q_folio)
    if q_tipo:
        movimientos = movimientos.filter(tipo=q_tipo)
    if q_divisa:
        movimientos = movimientos.filter(divisas__divisa_id=q_divisa)
    if q_beneficiario:
        movimientos = movimientos.filter(beneficiario__icontains=q_beneficiario)

    if not any([q_fecha, q_folio, q_tipo, q_divisa, q_beneficiario]) and ultimo_corte:
        movimientos = movimientos.filter(corte=ultimo_corte)

    movimientos = movimientos.distinct()[:200]

    apertura_form = AperturaCajaForm(initial={'fecha': timezone.localdate()})
    movimiento_form = MovimientoTesoreriaForm()
    cierre_form = CierreCajaForm(initial={'fecha_cierre': timezone.now()})
    modal_open = None
    divisas = Divisa.objects.filter(activa=True)
    corte_abierto = CorteCaja.objects.filter(cerrado=False).order_by('-fecha').first()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'apertura':
            apertura_form = AperturaCajaForm(request.POST)
            corte_abierto = CorteCaja.objects.filter(cerrado=False).order_by('-fecha').first()
            if corte_abierto:
                apertura_form.add_error(None, 'Debes cerrar el corte de caja abierto antes de iniciar uno nuevo.')
            elif apertura_form.is_valid():
                datos = apertura_form.cleaned_data
                try:
                    CorteCaja.abrir_nuevo_corte(
                        fecha=datos['fecha'],
                        responsable_apertura=datos['responsable_apertura'],
                        observaciones=datos['observaciones'],
                    )
                    messages.success(request, 'Corte de caja abierto correctamente.')
                    return redirect('index')
                except Exception as exc:
                    apertura_form.add_error(None, str(exc))
            modal_open = 'apertura'

        elif action == 'movimiento':
            movimiento_form = MovimientoTesoreriaForm(request.POST)
            corte_abierto = CorteCaja.objects.filter(cerrado=False).order_by('-fecha').first()
            if not corte_abierto:
                movimiento_form.add_error(None, 'No hay un corte de caja abierto. Abre un corte antes de registrar movimientos.')
            else:
                form_valid = movimiento_form.is_valid()
                if form_valid:
                    datos = movimiento_form.cleaned_data
                    divisa = datos['divisa']
                    cantidad = datos['cantidad']
                    if datos['tipo'] == MovimientoTesoreria.EGRESO:
                        saldo_caja = SaldoCaja.objects.filter(corte=corte_abierto, divisa=divisa).first()
                        disponible = saldo_caja.saldo_disponible if saldo_caja else Decimal('0')
                        if cantidad > disponible:
                            movimiento_form.add_error('cantidad', 'Saldo insuficiente para el egreso en esta divisa.')
                            messages.error(request, 'Saldo insuficiente para el egreso en esta divisa.')
                            form_valid = False

                if form_valid and not movimiento_form.errors:
                    datos = movimiento_form.cleaned_data
                    corte_abierto = CorteCaja.objects.filter(cerrado=False).order_by('-fecha').first()
                    movimiento = MovimientoTesoreria.objects.create(
                        corte=corte_abierto,
                        fecha=corte_abierto.fecha,
                        folio=datos['folio'],
                        tipo=datos['tipo'],
                        autorizo=datos['autorizo'],
                        beneficiario=datos['beneficiario'],
                        concepto=datos['concepto'],
                    )
                    MovimientoDivisa.objects.create(
                        movimiento=movimiento,
                        divisa=datos['divisa'],
                        cantidad=datos['cantidad'],
                    )
                    messages.success(request, 'Movimiento registrado correctamente.')
                    return redirect('index')

        elif action == 'cierre':
            cierre_form = CierreCajaForm(request.POST)
            corte_abierto = CorteCaja.objects.filter(cerrado=False).order_by('-fecha').first()
            if not corte_abierto:
                cierre_form.add_error(None, 'No hay un corte de caja abierto para cerrar.')
                modal_open = 'cierre'
            else:
                if cierre_form.is_valid():
                    datos = cierre_form.cleaned_data
                    fecha_cierre = datos.get('fecha_cierre') or timezone.now()
                    errores = False
                    valores_fisicos = []
                    for saldo in corte_abierto.saldos.all():
                        key = f'fisico_{saldo.id}'
                        if key in request.POST and request.POST[key].strip():
                            try:
                                saldo_fisico = Decimal(request.POST[key])
                            except (InvalidOperation, ValueError):
                                cierre_form.add_error(None, f'Saldo físico inválido para {saldo.divisa.codigo}.')
                                errores = True
                            else:
                                valores_fisicos.append((saldo, saldo_fisico))

                    if not cierre_form.errors and not errores:
                        for saldo, saldo_fisico in valores_fisicos:
                            saldo.registrar_saldo_fisico(saldo_fisico)

                        try:
                            corte_abierto.close(
                                responsable_cierre=datos['responsable_cierre'],
                                fecha_cierre=fecha_cierre,
                            )
                            messages.success(request, 'Corte de caja cerrado correctamente.')
                            return redirect('index')
                        except Exception as exc:
                            cierre_form.add_error(None, str(exc))
                modal_open = 'cierre'

    return render(request, 'index.html', {
        'ultimo_corte': ultimo_corte,
        'corte_abierto': corte_abierto,
        'saldos': saldos,
        'movimientos': movimientos,
        'divisas': divisas,
        'filtros': {
            'fecha': q_fecha,
            'folio': q_folio,
            'tipo': q_tipo,
            'divisa': q_divisa,
            'beneficiario': q_beneficiario,
        },
        'apertura_form': apertura_form,
        'movimiento_form': movimiento_form,
        'cierre_form': cierre_form,
        'modal_open': modal_open,
    })


@area_requerida(AREA_TESORERIA)
def apertura_caja(request):
    ultimo_corte = CorteCaja.objects.order_by('-fecha').first()
    if request.method == 'POST':
        form = AperturaCajaForm(request.POST)
        if form.is_valid():
            datos = form.cleaned_data
            try:
                CorteCaja.abrir_nuevo_corte(
                    fecha=datos['fecha'],
                    responsable_apertura=datos['responsable_apertura'],
                    observaciones=datos['observaciones'],
                )
                messages.success(request, 'Corte de caja abierto correctamente.')
                return redirect('index')
            except Exception as exc:
                form.add_error(None, str(exc))
    else:
        form = AperturaCajaForm(initial={'fecha': timezone.localdate()})

    return render(request, 'apertura_caja.html', {
        'form': form,
        'ultimo_corte': ultimo_corte,
    })


@area_requerida(AREA_TESORERIA)
def registro_movimiento(request):
    corte_abierto = CorteCaja.objects.filter(cerrado=False).order_by('-fecha').first()
    if not corte_abierto:
        messages.warning(request, 'No hay un corte de caja abierto. Debes abrir un corte antes de registrar movimientos.')
        return redirect('apertura_caja')

    if request.method == 'POST':
        form = MovimientoTesoreriaForm(request.POST)
        if form.is_valid():
            datos = form.cleaned_data
            divisa = datos['divisa']
            cantidad = datos['cantidad']
            if datos['tipo'] == MovimientoTesoreria.EGRESO:
                saldo_caja = SaldoCaja.objects.filter(corte=corte_abierto, divisa=divisa).first()
                disponible = saldo_caja.saldo_disponible if saldo_caja else 0
                if cantidad > disponible:
                    form.add_error('cantidad', 'Saldo insuficiente para el egreso en esta divisa.')
            if not form.errors:
                movimiento = MovimientoTesoreria.objects.create(
                    corte=corte_abierto,
                    fecha=corte_abierto.fecha,
                    folio=datos['folio'],
                    tipo=datos['tipo'],
                    autorizo=datos['autorizo'],
                    beneficiario=datos['beneficiario'],
                    concepto=datos['concepto'],
                )
                MovimientoDivisa.objects.create(
                    movimiento=movimiento,
                    divisa=divisa,
                    cantidad=cantidad,
                )
                messages.success(request, 'Movimiento registrado correctamente.')
                return redirect('consulta_movimientos')
    else:
        form = MovimientoTesoreriaForm()

    return render(request, 'movimiento_form.html', {
        'form': form,
        'corte_abierto': corte_abierto,
    })


@area_requerida(AREA_TESORERIA)
def consulta_movimientos(request):
    movimientos = MovimientoTesoreria.objects.prefetch_related('divisas__divisa').order_by('-fecha', '-creado')
    q_fecha = request.GET.get('fecha')
    q_folio = request.GET.get('folio')
    q_tipo = request.GET.get('tipo')
    q_divisa = request.GET.get('divisa')
    q_beneficiario = request.GET.get('beneficiario')

    if q_fecha:
        movimientos = movimientos.filter(fecha=q_fecha)
    if q_folio:
        movimientos = movimientos.filter(folio__icontains=q_folio)
    if q_tipo:
        movimientos = movimientos.filter(tipo=q_tipo)
    if q_divisa:
        movimientos = movimientos.filter(divisas__divisa_id=q_divisa)
    if q_beneficiario:
        movimientos = movimientos.filter(beneficiario__icontains=q_beneficiario)

    movimientos = movimientos.distinct()[:200]
    divisas = Divisa.objects.filter(activa=True)

    return render(request, 'movimiento_list.html', {
        'movimientos': movimientos,
        'divisas': divisas,
        'filtros': {
            'fecha': q_fecha,
            'folio': q_folio,
            'tipo': q_tipo,
            'divisa': q_divisa,
            'beneficiario': q_beneficiario,
        },
    })


@area_requerida(AREA_TESORERIA)
def cierre_caja(request):
    corte_abierto = CorteCaja.objects.filter(cerrado=False).order_by('-fecha').first()
    if not corte_abierto:
        messages.warning(request, 'No hay un corte de caja abierto para cerrar.')
        return redirect('index')

    saldos = corte_abierto.saldos.select_related('divisa')
    if request.method == 'POST':
        form = CierreCajaForm(request.POST)
        if form.is_valid():
            datos = form.cleaned_data
            fecha_cierre = datos.get('fecha_cierre') or timezone.now()
            try:
                for saldo in saldos:
                    key = f'fisico_{saldo.id}'
                    if key in request.POST and request.POST[key].strip():
                        saldo.registrar_saldo_fisico(request.POST[key])
                corte_abierto.close(
                    responsable_cierre=datos['responsable_cierre'],
                    fecha_cierre=fecha_cierre,
                )
                messages.success(request, 'Corte de caja cerrado correctamente.')
                return redirect('index')
            except Exception as exc:
                form.add_error(None, str(exc))
    else:
        form = CierreCajaForm(initial={'fecha_cierre': timezone.now()})

    return render(request, 'cierre_caja.html', {
        'corte': corte_abierto,
        'saldos': saldos,
        'form': form,
    })


@area_requerida(AREA_TESORERIA)
def administrar_divisas(request):
    divisas = Divisa.objects.order_by('codigo')
    if request.method == 'POST':
        form = DivisaForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Divisa registrada correctamente.')
            return redirect('administrar_divisas')
    else:
        form = DivisaForm()

    return render(request, 'divisas.html', {
        'divisas': divisas,
        'form': form,
    })
