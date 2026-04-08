import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import axios from 'axios';
import JsonEditor from './JsonEditor';
import { Check, Edit, X, ArrowLeft } from 'lucide-react';

export default function Review() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [invoice, setInvoice] = useState(null);
    const [editedData, setEditedData] = useState(null);
    const [zohoItems, setZohoItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        fetchInvoiceData();
    }, [id]);

    const fetchInvoiceData = async () => {
        try {
            const [invoiceRes, itemsRes] = await Promise.all([
                axios.get(`http://127.0.0.1:8000/verification/invoice/${id}`),
                axios.get(`http://127.0.0.1:8000/items`)
            ]);
            setInvoice(invoiceRes.data);
            setZohoItems(itemsRes.data);

            // Initialize edited data with the fetched data
            const initialData = JSON.parse(JSON.stringify(invoiceRes.data.invoice_data));
            // Ensure item_id exists in line_items so it's editable
            if (initialData.line_items && Array.isArray(initialData.line_items)) {
                initialData.line_items = initialData.line_items.map(item => ({
                    ...item,
                    item_id: item.item_id || ""
                }));
            }
            setEditedData(initialData);
        } catch (err) {
            console.error(err);
            setError('Failed to fetch invoice data.');
        } finally {
            setLoading(false);
        }
    };

    const handleAction = async (action) => {
        try {
            setLoading(true);
            const payload = { action };
            if (action === 'edit') {
                payload.data = editedData;
            }

            await axios.post(`http://127.0.0.1:8000/verification/invoice/${id}/action`, payload);
            navigate('/');
        } catch (err) {
            console.error(err);
            alert('Error performing action: ' + (err.response?.data?.detail || err.message));
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="flex justify-center items-center h-64">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
            </div>
        );
    }

    if (error) {
        return <div className="text-red-500 font-bold text-center mt-10">{error}</div>;
    }

    if (!invoice) return null;

    return (
        <div className="bg-white shadow overflow-hidden sm:rounded-lg mb-8">
            <div className="px-4 py-5 sm:px-6 flex items-center justify-between">
                <div>
                    <button onClick={() => navigate('/')} className="mb-4 text-indigo-600 hover:text-indigo-900 flex items-center text-sm font-medium">
                        <ArrowLeft className="w-4 h-4 mr-1" /> Back to Dashboard
                    </button>
                    <h3 className="text-lg leading-6 font-medium text-slate-900">Review Invoice: {id}</h3>
                    <p className="mt-1 max-w-2xl text-sm text-slate-500">Status: {invoice.status}</p>
                </div>
                <div className="flex space-x-3">
                    <button
                        onClick={() => handleAction('reject')}
                        className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-red-600 hover:bg-red-700 shadow-sm transition"
                    >
                        <X className="mr-2 h-4 w-4" /> Reject
                    </button>
                    <button
                        onClick={() => handleAction('edit')}
                        className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 shadow-sm transition"
                    >
                        <Edit className="mr-2 h-4 w-4" /> Save Edit & Send
                    </button>
                    <button
                        onClick={() => handleAction('accept')}
                        className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-green-600 hover:bg-green-700 shadow-sm transition"
                    >
                        <Check className="mr-2 h-4 w-4" /> Accept & Send
                    </button>
                </div>
            </div>

            <div className="border-t border-slate-200">
                <JsonEditor
                    data={editedData}
                    onChange={setEditedData}
                    zohoItems={zohoItems}
                />
            </div>
        </div>
    );
}
