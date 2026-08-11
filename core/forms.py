from django import forms
from django.utils import timezone

from treasury.models import Divisa, MovimientoTesoreria


class AperturaCajaForm(forms.Form):
    fecha = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label='Fecha de corte',
    )
    responsable_apertura = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label='Responsable de apertura',
    )
    observaciones = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label='Observaciones',
    )


class MovimientoTesoreriaForm(forms.Form):
    tipo = forms.ChoiceField(
        choices=[
            (MovimientoTesoreria.INGRESO, 'Ingreso'),
            (MovimientoTesoreria.EGRESO, 'Egreso'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Tipo de movimiento',
    )
    folio = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    autorizo = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    beneficiario = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    concepto = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
    )
    divisa = forms.ModelChoiceField(
        queryset=Divisa.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['divisa'].queryset = Divisa.objects.filter(activa=True)
    cantidad = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=0.01,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    )


class CierreCajaForm(forms.Form):
    responsable_cierre = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label='Responsable de cierre',
    )
    fecha_cierre = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
        initial=timezone.now,
        label='Fecha de cierre',
    )


class DivisaForm(forms.ModelForm):
    class Meta:
        model = Divisa
        fields = ['codigo', 'nombre', 'simbolo', 'activa']
        widgets = {
            'codigo': forms.TextInput(attrs={'class': 'form-control'}),
            'nombre': forms.TextInput(attrs={'class': 'form-control'}),
            'simbolo': forms.TextInput(attrs={'class': 'form-control'}),
            'activa': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
